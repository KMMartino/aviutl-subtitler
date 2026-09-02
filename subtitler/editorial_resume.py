"""Compatibility-aware reuse and invalidation for editorial checkpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence, cast

from .editorial_project import (
    CHECKPOINT_STAGES,
    PROJECT_CHECKPOINT_STAGES,
    EDITORIAL_PIPELINE_STAGES,
    EDITORIAL_STAGE_VERSIONS,
    GLOBAL_CHECKPOINT_STAGE,
    ACTION_CHECKPOINT_STAGE,
    fingerprint_source,
    unresolved_editorial_sources,
)
from .errors import SubtitlerError


ResumeBoundary = Literal[
    "source_probe",
    "transcription",
    "visual_learning",
    "semantic_spans",
    "local_reconciliation",
    "global_reconciliation",
    "action_planning",
    "editorial_assets",
]
ResumeMode = Literal["compatible", *EDITORIAL_PIPELINE_STAGES]


def inspect_editorial_resume(
    artifact: dict[str, Any], source_specs: Sequence[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Describe source identity, version compatibility, and safe restart choices."""
    matched_selected_indices: list[int] = []
    matched_artifact_indices: list[int] = []
    source_error = ""
    if source_specs is None:
        matches_sources, source_error = _sources_match(artifact, None)
        if matches_sources:
            matched_artifact_indices = list(range(len(artifact["sources"])))
    else:
        matched_selected_indices, matched_artifact_indices, source_error = _associated_sources(
            artifact, source_specs
        )
        matches_sources = bool(matched_selected_indices)
    required = first_incompatible_boundary(artifact)
    next_incomplete = _next_incomplete_checkpoint(artifact)
    complete = next_incomplete is None
    if required is not None:
        recommended = required
    elif complete:
        recommended = ACTION_CHECKPOINT_STAGE
    else:
        recommended = "compatible"
    if required is None:
        available = ["compatible", *EDITORIAL_PIPELINE_STAGES]
    else:
        available = list(EDITORIAL_PIPELINE_STAGES[: _stage_index(required) + 1])
    artifact_sources = _artifact_source_selections(
        artifact,
        source_specs,
        matched_selected_indices,
        matched_artifact_indices,
    )
    full_match = bool(source_specs is None and matches_sources) or (
        source_specs is not None
        and len(matched_selected_indices) == len(source_specs) == len(artifact_sources)
    )
    return {
        "project_id": artifact["project_id"],
        "artifact_status": artifact["editorial_map"]["status"],
        "matches_sources": matches_sources,
        "match_kind": "full" if full_match else "partial" if matches_sources else "none",
        "matched_source_count": len(matched_selected_indices) if source_specs is not None else len(matched_artifact_indices),
        "matched_selected_indices": matched_selected_indices,
        "source_error": source_error,
        "required_restart_from": required,
        "next_incomplete": next_incomplete,
        "recommended_restart_from": recommended,
        "available_restart_from": available,
        "artifact_versions": dict(artifact["pipeline_versions"]),
        "current_versions": dict(EDITORIAL_STAGE_VERSIONS),
        "project_request": {
            "sources": artifact_sources,
            "titleOrGame": artifact["title_or_game"],
            "objective": artifact["objective"],
            "targetDurationMinSeconds": artifact["target_duration_min_ms"] / 1000.0,
            "targetDurationMaxSeconds": artifact["target_duration_max_ms"] / 1000.0,
            "outputLocale": artifact.get("output_locale", "en"),
        },
    }


def first_incompatible_boundary(artifact: dict[str, Any]) -> ResumeBoundary | None:
    recorded = artifact["pipeline_versions"]
    for stage in EDITORIAL_PIPELINE_STAGES:
        if recorded.get(stage) != EDITORIAL_STAGE_VERSIONS[stage]:
            return cast(ResumeBoundary, stage)
    return None


def prepare_editorial_resume(
    artifact: dict[str, Any], restart_from: str | None = None
) -> ResumeBoundary | None:
    """Invalidate only the required suffix of the versioned pipeline.

    ``compatible`` preserves exact per-source completion state after applying
    mandatory version invalidation. An explicit boundary may move the restart
    earlier, but never later than the first incompatible boundary.
    """
    if restart_from is not None and restart_from not in {"compatible", *EDITORIAL_PIPELINE_STAGES}:
        raise SubtitlerError(f"Unknown editorial restart boundary: {restart_from}")
    required = first_incompatible_boundary(artifact)
    explicit = None if restart_from in {None, "compatible"} else cast(ResumeBoundary, restart_from)
    if required is not None and explicit is not None and _stage_index(explicit) > _stage_index(required):
        raise SubtitlerError(
            f"Checkpoint compatibility requires restarting from {required} or an earlier boundary"
        )
    selected = required
    if explicit is not None and (selected is None or _stage_index(explicit) < _stage_index(selected)):
        selected = explicit
    if selected is not None:
        invalidate_editorial_from(artifact, selected)
    return selected


def relink_matching_editorial_sources(
    artifact: dict[str, Any], source_specs: Sequence[dict[str, Any]]
) -> None:
    matches, error = _sources_match(artifact, source_specs)
    if not matches:
        raise SubtitlerError(error or "Selected files do not match the editorial checkpoint")
    ordered = sorted(artifact["sources"], key=lambda item: item["order"])
    for source, supplied in zip(ordered, source_specs):
        audio_path = str(Path(str(supplied["audioPath"])).resolve())
        visual_path = str(Path(str(supplied["visualPath"])).resolve())
        source["audio_path"] = audio_path
        source["visual_path"] = visual_path
        source["path"] = visual_path


def relink_matching_editorial_prefix(
    artifact: dict[str, Any], source_specs: Sequence[dict[str, Any]]
) -> None:
    ordered = sorted(artifact["sources"], key=lambda item: item["order"])
    if len(source_specs) != len(ordered):
        raise SubtitlerError("The existing recordings must remain the chronological prefix")
    for source, supplied in zip(ordered, source_specs):
        matches, error = _source_matches(source, supplied)
        if not matches:
            raise SubtitlerError(error or "An existing recording does not match the checkpoint")
        audio_path = str(Path(str(supplied["audioPath"])).resolve())
        visual_path = str(Path(str(supplied["visualPath"])).resolve())
        source["audio_path"] = audio_path
        source["visual_path"] = visual_path
        source["path"] = visual_path


def invalidate_editorial_from(artifact: dict[str, Any], boundary: ResumeBoundary) -> None:
    boundary_index = _stage_index(boundary)
    now = datetime.now(timezone.utc).isoformat()
    _discard_invalidated_stage_costs(artifact, boundary_index)
    for stage in EDITORIAL_PIPELINE_STAGES[boundary_index:]:
        artifact["pipeline_versions"][stage] = EDITORIAL_STAGE_VERSIONS[stage]
    if boundary in CHECKPOINT_STAGES:
        source_boundary = CHECKPOINT_STAGES.index(boundary)
        for source in artifact["sources"]:
            for stage in CHECKPOINT_STAGES[source_boundary:]:
                source["stages"][stage] = _reset_checkpoint(
                    source["stages"][stage], EDITORIAL_STAGE_VERSIONS[stage]
                )
            source["status"] = _source_status(source["stages"])
        if source_boundary <= CHECKPOINT_STAGES.index("semantic_spans"):
            artifact["cumulative_context"] = _empty_cumulative_context()
        if source_boundary <= CHECKPOINT_STAGES.index("local_reconciliation"):
            for source in artifact["sources"]:
                source["result"] = None
            for field in (
                "global_threads",
                "recommendations",
                "narration_briefs",
                "creative_suggestions",
                "emphasized_phrases",
                "timeline_coverage",
                "connections",
                "conflicts",
            ):
                artifact["editorial_map"][field] = []
    global_index = _stage_index(GLOBAL_CHECKPOINT_STAGE)
    action_index = _stage_index(ACTION_CHECKPOINT_STAGE)
    if boundary_index <= global_index:
        for field in (
            "global_threads",
            "connections",
            "conflicts",
            "duration_budget",
            "editorial_direction_summary",
            "optimal_plan",
            "director_review",
            "director_model",
            "final_actions",
            "supporting_edits",
            "editorial_threads",
            "payoff_threads",
            "story_actions",
            "emphasized_phrases",
            "workflow",
            "protected_zones",
            "cut_candidates",
            "confirmed_cuts",
            "removed_ms",
            "narration_replaced_ms",
            "prompt_version",
        ):
            artifact["editorial_map"][field] = [] if field in {
                "global_threads", "connections", "conflicts", "optimal_plan",
                "final_actions", "supporting_edits", "editorial_threads",
                "payoff_threads", "story_actions", "emphasized_phrases",
                "protected_zones", "cut_candidates", "confirmed_cuts",
            } else None
    elif boundary_index <= action_index:
        for field in (
            "director_review",
            "director_model",
            "final_actions",
            "supporting_edits",
            "editorial_threads",
            "story_actions",
            "emphasized_phrases",
            "workflow",
            "protected_zones",
            "cut_candidates",
            "confirmed_cuts",
            "removed_ms",
            "narration_replaced_ms",
            "prompt_version",
        ):
            artifact["editorial_map"][field] = [] if field in {
                "final_actions", "supporting_edits", "editorial_threads", "story_actions",
                "emphasized_phrases", "protected_zones", "cut_candidates", "confirmed_cuts",
            } else None
    else:
        action_output = artifact["editorial_map"].get(ACTION_CHECKPOINT_STAGE, {}).get("output")
        if isinstance(action_output, dict):
            artifact["editorial_map"]["supporting_edits"] = list(action_output.get("supporting_edits", []))
    artifact["editorial_map"]["assets"] = []
    for stage in PROJECT_CHECKPOINT_STAGES:
        if _stage_index(stage) < boundary_index:
            continue
        checkpoint = artifact["editorial_map"][stage]
        artifact["editorial_map"][stage] = _reset_checkpoint(
            checkpoint, EDITORIAL_STAGE_VERSIONS[stage]
        )
    artifact["editorial_map"]["status"] = "pending"
    artifact["updated_at_utc"] = now
    artifact["run_provenance"].setdefault("invalidations", []).append(
        {
            "at_utc": now,
            "restart_from": boundary,
            "pipeline_versions": dict(EDITORIAL_STAGE_VERSIONS),
        }
    )


def _discard_invalidated_stage_costs(
    artifact: dict[str, Any], boundary_index: int
) -> None:
    """Keep only costs belonging to the currently reusable pipeline prefix."""
    provenance = artifact.setdefault("run_provenance", {})
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in provenance.get("runs", []):
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_id") or "")
        stage = str(raw.get("stage") or "")
        if not source_id or stage not in EDITORIAL_PIPELINE_STAGES:
            continue
        if _stage_index(cast(ResumeBoundary, stage)) >= boundary_index:
            continue
        try:
            cost = max(0.0, float(raw.get("actual_cost_usd", 0.0)))
        except (TypeError, ValueError):
            cost = 0.0
        latest[(source_id, stage)] = {
            "source_id": source_id,
            "stage": stage,
            "actual_cost_usd": cost,
        }
    provenance["runs"] = list(latest.values())
    provenance["actual_cost_usd"] = sum(
        float(row["actual_cost_usd"]) for row in provenance["runs"]
    )


def _sources_match(
    artifact: dict[str, Any], source_specs: Sequence[dict[str, Any]] | None
) -> tuple[bool, str]:
    if source_specs is None:
        unresolved = unresolved_editorial_sources(artifact)
        if unresolved:
            return False, "One or more checkpoint source files are missing or changed."
        return True, ""
    ordered = sorted(artifact["sources"], key=lambda item: item["order"])
    if len(source_specs) != len(ordered):
        return False, "The selected source count does not match this checkpoint."
    try:
        for expected, supplied in zip(ordered, source_specs):
            if supplied.get("mode") != expected["media_mode"]:
                return False, "A selected source has a different single/paired structure."
            roles = ("visual",) if expected["media_mode"] == "single" else ("audio", "visual")
            for role in roles:
                raw_path = supplied.get(f"{role}Path")
                if not isinstance(raw_path, str) or not raw_path:
                    return False, f"The selected source lacks its {role} file."
                fingerprint = expected[f"{role}_fingerprint"]
                actual = fingerprint_source(
                    Path(raw_path).resolve(),
                    sample_size=int(fingerprint["sample_size_bytes"]),
                )
                if actual.digest != fingerprint["digest"] or actual.size_bytes != fingerprint["size_bytes"]:
                    return False, f"The selected {role} file does not match the checkpoint fingerprint."
    except SubtitlerError as exc:
        return False, str(exc)
    return True, ""


def _associated_sources(
    artifact: dict[str, Any], source_specs: Sequence[dict[str, Any]]
) -> tuple[list[int], list[int], str]:
    ordered = sorted(artifact["sources"], key=lambda item: item["order"])
    matched_selected: list[int] = []
    matched_artifact: list[int] = []
    used_artifact: set[int] = set()
    last_error = ""
    for selected_index, supplied in enumerate(source_specs):
        for artifact_index, expected in enumerate(ordered):
            if artifact_index in used_artifact:
                continue
            matches, error = _source_matches(expected, supplied)
            if matches:
                matched_selected.append(selected_index)
                matched_artifact.append(artifact_index)
                used_artifact.add(artifact_index)
                break
            if error:
                last_error = error
    if matched_selected:
        return matched_selected, matched_artifact, ""
    return [], [], last_error or "None of the selected recordings match this checkpoint."


def _source_matches(expected: dict[str, Any], supplied: dict[str, Any]) -> tuple[bool, str]:
    if supplied.get("mode") != expected["media_mode"]:
        return False, "A selected source has a different single/paired structure."
    roles = ("visual",) if expected["media_mode"] == "single" else ("audio", "visual")
    try:
        for role in roles:
            raw_path = supplied.get(f"{role}Path")
            if not isinstance(raw_path, str) or not raw_path:
                return False, f"The selected source lacks its {role} file."
            fingerprint = expected[f"{role}_fingerprint"]
            actual = fingerprint_source(
                Path(raw_path).resolve(),
                sample_size=int(fingerprint["sample_size_bytes"]),
            )
            if actual.digest != fingerprint["digest"] or actual.size_bytes != fingerprint["size_bytes"]:
                return False, f"The selected {role} file does not match the checkpoint fingerprint."
    except SubtitlerError as exc:
        return False, str(exc)
    return True, ""


def _artifact_source_selections(
    artifact: dict[str, Any],
    supplied: Sequence[dict[str, Any]] | None,
    matched_selected: Sequence[int],
    matched_artifact: Sequence[int],
) -> list[dict[str, Any]]:
    selected_by_artifact = {
        artifact_index: supplied[selected_index]
        for selected_index, artifact_index in zip(matched_selected, matched_artifact)
    } if supplied is not None else {}
    result = []
    for artifact_index, source in enumerate(sorted(artifact["sources"], key=lambda item: item["order"])):
        replacement = selected_by_artifact.get(artifact_index, {})
        audio_path = str(replacement.get("audioPath") or source["audio_path"])
        visual_path = str(replacement.get("visualPath") or source["visual_path"])
        duration = source["visual_duration_ms"] / 1000.0
        result.append(
            {
                "path": visual_path,
                "durationSeconds": duration,
                "mode": source["media_mode"],
                "audioPath": audio_path,
                "visualPath": visual_path,
                "audioDurationSeconds": source["audio_duration_ms"] / 1000.0,
                "visualDurationSeconds": duration,
                "width": None,
                "height": None,
                "audioWidth": None,
                "audioHeight": None,
                "frameRate": source["frame_rate"],
                "audioFrameRate": source["frame_rate"],
                "pairingBasis": source["pairing_basis"],
                "roleConfirmed": True,
            }
        )
    return result


def _next_incomplete_checkpoint(artifact: dict[str, Any]) -> dict[str, Any] | None:
    for source in sorted(artifact["sources"], key=lambda item: item["order"]):
        for stage in CHECKPOINT_STAGES:
            checkpoint = source["stages"][stage]
            if checkpoint["status"] != "complete":
                return {
                    "source_id": source["source_id"],
                    "source_name": source["original_name"],
                    "stage": stage,
                    "status": checkpoint["status"],
                }
    for stage in PROJECT_CHECKPOINT_STAGES:
        checkpoint = artifact["editorial_map"][stage]
        if checkpoint["status"] != "complete":
            return {
                "source_id": "project",
                "source_name": artifact["title_or_game"],
                "stage": stage,
                "status": checkpoint["status"],
            }
    return None


def _reset_checkpoint(checkpoint: dict[str, Any], version: int) -> dict[str, Any]:
    return {
        "version": version,
        "status": "pending",
        "attempts": int(checkpoint.get("attempts", 0)),
        "started_at_utc": None,
        "completed_at_utc": None,
        "error": "",
        "output": None,
    }


def _source_status(stages: dict[str, dict[str, Any]]) -> str:
    statuses = {checkpoint["status"] for checkpoint in stages.values()}
    if statuses == {"complete"}:
        return "complete"
    if "failed" in statuses:
        return "failed"
    if "in_progress" in statuses or "complete" in statuses:
        return "in_progress"
    return "pending"


def _empty_cumulative_context() -> dict[str, list[Any]]:
    return {
        "current_objectives": [],
        "completed_milestones": [],
        "open_threads": [],
        "recurring_locations_entities_mechanics": [],
        "known_repetition_patterns": [],
        "creator_stance_and_sentiment": [],
        "retrieval_index": [],
    }


def _stage_index(stage: str) -> int:
    return EDITORIAL_PIPELINE_STAGES.index(stage)
