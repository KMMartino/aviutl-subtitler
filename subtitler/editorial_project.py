"""Durable, source-independent project state for long-form editorial analysis."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
from uuid import uuid4

from .errors import SubtitlerError


EDITORIAL_SCHEMA_VERSION = 3
FINGERPRINT_ALGORITHM = "sha256-sampled-v1"
CHECKPOINT_STAGES = (
    "source_probe",
    "transcription",
    "visual_learning",
    "semantic_spans",
    "local_reconciliation",
)
GLOBAL_CHECKPOINT_STAGE = "global_reconciliation"
EDITORIAL_PIPELINE_STAGES = (*CHECKPOINT_STAGES, GLOBAL_CHECKPOINT_STAGE)
# Increment the matching boundary version whenever its artifact contract or
# behavior changes. See AGENTS.md for the mandatory maintenance rule.
EDITORIAL_STAGE_VERSIONS: dict[str, int] = {
    "source_probe": 1,
    "transcription": 5,
    "visual_learning": 5,
    "semantic_spans": 2,
    "local_reconciliation": 1,
    "global_reconciliation": 4,
}
LEGACY_EDITORIAL_STAGE_VERSIONS = {stage: 1 for stage in EDITORIAL_PIPELINE_STAGES}
CheckpointStatus = Literal["pending", "in_progress", "complete", "failed"]


@dataclass(frozen=True)
class SourceFingerprint:
    algorithm: str
    size_bytes: int
    digest: str
    sample_size_bytes: int


@dataclass(frozen=True)
class EditorialSourceInput:
    path: Path
    duration_ms: int
    audio_path: Path | None = None
    visual_path: Path | None = None
    audio_duration_ms: int | None = None
    visual_duration_ms: int | None = None
    frame_rate: float | None = None
    media_mode: Literal["single", "paired"] = "single"
    pairing_basis: Literal["single", "filename", "resolution", "manual"] = "single"


@dataclass(frozen=True)
class EditorialProjectOptions:
    title_or_game: str
    objective: str
    target_duration_min_ms: int
    target_duration_max_ms: int
    must_keep_notes: tuple[str, ...] = ()
    de_emphasize_notes: tuple[str, ...] = ()
    subtitle_mode: Literal["full", "emphasis"] = "full"
    output_locale: Literal["en", "ja"] = "en"


def fingerprint_source(path: Path, *, sample_size: int = 1024 * 1024) -> SourceFingerprint:
    """Fingerprint large media without reading the full file.

    The digest is independent of filename and timestamps so moved or renamed
    source media can be relinked. Size plus evenly distributed samples guard
    against accidentally accepting a different recording with the same name.
    """
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SubtitlerError(f"Could not inspect editorial source {path}: {exc}") from exc
    if not path.is_file():
        raise SubtitlerError(f"Editorial source is not a file: {path}")

    hasher = hashlib.sha256()
    hasher.update(FINGERPRINT_ALGORITHM.encode("ascii"))
    hasher.update(size.to_bytes(16, "big", signed=False))
    offsets = _sample_offsets(size, sample_size)
    try:
        with path.open("rb") as handle:
            for offset in offsets:
                handle.seek(offset)
                data = handle.read(min(sample_size, size - offset))
                hasher.update(offset.to_bytes(16, "big", signed=False))
                hasher.update(len(data).to_bytes(8, "big", signed=False))
                hasher.update(data)
    except OSError as exc:
        raise SubtitlerError(f"Could not fingerprint editorial source {path}: {exc}") from exc
    return SourceFingerprint(
        algorithm=FINGERPRINT_ALGORITHM,
        size_bytes=size,
        digest=hasher.hexdigest(),
        sample_size_bytes=sample_size,
    )


def create_editorial_project(
    sources: Sequence[EditorialSourceInput],
    options: EditorialProjectOptions,
    *,
    project_id: str | None = None,
    now_utc: str | None = None,
) -> dict[str, Any]:
    """Create the canonical checkpoint artifact for an ordered source list."""
    _validate_options(options)
    if not sources:
        raise SubtitlerError("Editorial analysis requires at least one source file")
    normalized_paths: set[str] = set()
    source_records: list[dict[str, Any]] = []
    for order, source in enumerate(sources):
        visual = (source.visual_path or source.path).resolve()
        audio = (source.audio_path or visual).resolve()
        if source.media_mode not in {"single", "paired"}:
            raise SubtitlerError(f"Unknown editorial media mode: {source.media_mode}")
        if source.pairing_basis not in {"single", "filename", "resolution", "manual"}:
            raise SubtitlerError(f"Unknown editorial pairing basis: {source.pairing_basis}")
        if source.media_mode == "single" and audio != visual:
            raise SubtitlerError("Single-file editorial sources must use the same audio and visual media")
        if source.media_mode == "single" and source.pairing_basis != "single":
            raise SubtitlerError("Single-file editorial sources must use the single pairing basis")
        if source.media_mode == "paired" and audio == visual:
            raise SubtitlerError("Paired editorial sources require different audio and visual files")
        if source.media_mode == "paired" and source.pairing_basis == "single":
            raise SubtitlerError("Paired editorial sources require a pairing basis")
        if source.duration_ms <= 0:
            raise SubtitlerError(f"Editorial source duration must be positive: {visual}")
        audio_duration_ms = source.audio_duration_ms or source.duration_ms
        visual_duration_ms = source.visual_duration_ms or source.duration_ms
        if audio_duration_ms <= 0 or visual_duration_ms <= 0:
            raise SubtitlerError("Editorial audio and visual durations must be positive")
        if source.media_mode == "paired":
            if source.frame_rate is None or source.frame_rate <= 0:
                raise SubtitlerError("Paired editorial sources require a positive visual frame rate")
            tolerance_ms = (10.0 / source.frame_rate) * 1000.0 + 1.0
            if abs(audio_duration_ms - visual_duration_ms) > tolerance_ms:
                raise SubtitlerError("Paired editorial sources differ in length by more than 10 frames")
        for resolved in {audio, visual}:
            normalized = os.path.normcase(str(resolved))
            if normalized in normalized_paths:
                raise SubtitlerError(f"Editorial source was selected more than once: {resolved}")
            normalized_paths.add(normalized)
        visual_fingerprint = fingerprint_source(visual)
        audio_fingerprint = visual_fingerprint if audio == visual else fingerprint_source(audio)
        identity = hashlib.sha256(
            f"{visual_fingerprint.digest}:{audio_fingerprint.digest}".encode("ascii")
        ).hexdigest()
        source_records.append(
            {
                "source_id": f"source-{order + 1:04d}-{identity[:12]}",
                "order": order,
                "path": str(visual),
                "original_name": visual.name,
                "duration_ms": visual_duration_ms,
                "fingerprint": asdict(visual_fingerprint),
                "media_mode": source.media_mode,
                "pairing_basis": source.pairing_basis,
                "audio_path": str(audio),
                "visual_path": str(visual),
                "audio_original_name": audio.name,
                "visual_original_name": visual.name,
                "audio_duration_ms": audio_duration_ms,
                "visual_duration_ms": visual_duration_ms,
                "frame_rate": source.frame_rate,
                "audio_fingerprint": asdict(audio_fingerprint),
                "visual_fingerprint": asdict(visual_fingerprint),
                "status": "pending",
                "stages": {stage: _new_stage_checkpoint(stage) for stage in CHECKPOINT_STAGES},
                "result": None,
            }
        )
    created_at = now_utc or _utc_now()
    artifact: dict[str, Any] = {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "project_id": project_id or str(uuid4()),
        "created_at_utc": created_at,
        "updated_at_utc": created_at,
        "title_or_game": options.title_or_game.strip(),
        "objective": options.objective.strip(),
        "target_duration_min_ms": options.target_duration_min_ms,
        "target_duration_max_ms": options.target_duration_max_ms,
        "must_keep_notes": _clean_notes(options.must_keep_notes),
        "de_emphasize_notes": _clean_notes(options.de_emphasize_notes),
        "subtitle_mode": options.subtitle_mode,
        "output_locale": options.output_locale,
        "pipeline_versions": dict(EDITORIAL_STAGE_VERSIONS),
        "sources": source_records,
        "cumulative_context": _empty_cumulative_context(),
        "editorial_map": {
            "status": "pending",
            "global_reconciliation": _new_stage_checkpoint(GLOBAL_CHECKPOINT_STAGE),
            "global_threads": [],
            "recommendations": [],
            "narration_briefs": [],
            "creative_suggestions": [],
            "emphasized_phrases": [],
            "timeline_coverage": [],
            "connections": [],
            "conflicts": [],
            "duration_budget": None,
            "editorial_direction_summary": None,
            "optimal_plan": [],
            "director_review": None,
            "director_model": None,
        },
        "run_provenance": {
            "runs": [],
            "actual_cost_usd": 0.0,
            "max_cost_per_source_hour_usd": 10.0,
        },
    }
    validate_editorial_project(artifact)
    return artifact


def extend_editorial_project(
    artifact: dict[str, Any],
    sources: Sequence[EditorialSourceInput],
    options: EditorialProjectOptions,
) -> None:
    """Append chronological follow-ups without rebuilding completed source artifacts."""
    if not sources:
        artifact["title_or_game"] = options.title_or_game.strip()
        artifact["objective"] = options.objective.strip()
        artifact["target_duration_min_ms"] = options.target_duration_min_ms
        artifact["target_duration_max_ms"] = options.target_duration_max_ms
        artifact["must_keep_notes"] = _clean_notes(options.must_keep_notes)
        artifact["de_emphasize_notes"] = _clean_notes(options.de_emphasize_notes)
        artifact["subtitle_mode"] = options.subtitle_mode
        return
    temporary = create_editorial_project(sources, options)
    existing_paths = {
        os.path.normcase(source[f"{role}_path"])
        for source in artifact["sources"]
        for role in (("visual",) if source["media_mode"] == "single" else ("audio", "visual"))
    }
    existing_identities = {
        (source["visual_fingerprint"]["digest"], source["audio_fingerprint"]["digest"])
        for source in artifact["sources"]
    }
    offset = len(artifact["sources"])
    for relative_order, source in enumerate(temporary["sources"]):
        roles = ("visual",) if source["media_mode"] == "single" else ("audio", "visual")
        if any(os.path.normcase(source[f"{role}_path"]) in existing_paths for role in roles):
            raise SubtitlerError("A follow-up recording is already present in this editorial project")
        identity = (
            source["visual_fingerprint"]["digest"],
            source["audio_fingerprint"]["digest"],
        )
        if identity in existing_identities:
            raise SubtitlerError("A follow-up recording duplicates media already analyzed in this project")
        order = offset + relative_order
        source["order"] = order
        source["source_id"] = f"source-{order + 1:04d}-{source['source_id'].rsplit('-', 1)[-1]}"
        artifact["sources"].append(source)
        existing_identities.add(identity)
        for role in roles:
            existing_paths.add(os.path.normcase(source[f"{role}_path"]))
    artifact["title_or_game"] = options.title_or_game.strip()
    artifact["objective"] = options.objective.strip()
    artifact["target_duration_min_ms"] = options.target_duration_min_ms
    artifact["target_duration_max_ms"] = options.target_duration_max_ms
    artifact["must_keep_notes"] = _clean_notes(options.must_keep_notes)
    artifact["de_emphasize_notes"] = _clean_notes(options.de_emphasize_notes)
    artifact["subtitle_mode"] = options.subtitle_mode


def write_editorial_checkpoint(path: Path, artifact: dict[str, Any]) -> None:
    """Validate and atomically replace a project checkpoint."""
    validate_editorial_project(artifact)
    artifact["updated_at_utc"] = _utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(artifact, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def load_editorial_checkpoint(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SubtitlerError(f"Editorial checkpoint not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubtitlerError(f"Could not read editorial checkpoint {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SubtitlerError("Editorial checkpoint must contain a JSON object")
    _upgrade_legacy_checkpoint(value)
    _upgrade_output_locale(value)
    _upgrade_versionless_boundaries(value)
    _upgrade_editorial_map_fields(value)
    validate_editorial_project(value)
    return value


def update_source_stage(
    artifact: dict[str, Any],
    source_id: str,
    stage: str,
    status: CheckpointStatus,
    *,
    output: Any = None,
    error: str = "",
) -> None:
    """Record durable stage progress without discarding earlier completed work."""
    if stage not in CHECKPOINT_STAGES:
        raise SubtitlerError(f"Unknown editorial checkpoint stage: {stage}")
    if status not in {"pending", "in_progress", "complete", "failed"}:
        raise SubtitlerError(f"Unknown editorial checkpoint status: {status}")
    source = _source_by_id(artifact, source_id)
    current = source["stages"][stage]
    if current["status"] == "complete" and status != "complete":
        raise SubtitlerError(f"Completed editorial stage cannot move backward: {source_id}/{stage}")
    now = _utc_now()
    if status == "in_progress":
        current["attempts"] += 1
        current["started_at_utc"] = now
        current["completed_at_utc"] = None
    elif status == "complete":
        current["completed_at_utc"] = now
        current["error"] = ""
    elif status == "failed":
        current["completed_at_utc"] = now
        current["error"] = error.strip()[:4000]
    current["status"] = status
    if output is not None:
        current["output"] = output
    source["status"] = _derive_source_status(source["stages"])
    artifact["updated_at_utc"] = now


def relink_editorial_source(
    artifact: dict[str, Any], source_id: str, candidate_path: Path, *, role: Literal["audio", "visual"] = "visual"
) -> None:
    """Relink a moved source only when its mandatory fingerprint matches."""
    source = _source_by_id(artifact, source_id)
    expected = source[f"{role}_fingerprint"]
    actual = fingerprint_source(
        candidate_path.resolve(), sample_size=int(expected["sample_size_bytes"])
    )
    if actual.algorithm != expected["algorithm"] or actual.size_bytes != expected["size_bytes"] or actual.digest != expected["digest"]:
        raise SubtitlerError(
            f"Selected file does not match the checkpoint {role} fingerprint for {source[f'{role}_original_name']}"
        )
    source[f"{role}_path"] = str(candidate_path.resolve())
    if role == "visual":
        source["path"] = str(candidate_path.resolve())
    artifact["updated_at_utc"] = _utc_now()


def unresolved_editorial_sources(artifact: dict[str, Any]) -> list[dict[str, str]]:
    """Return missing or mismatched sources that must be relinked before resume."""
    unresolved: list[dict[str, str]] = []
    for source in artifact["sources"]:
        roles = ("visual",) if source["media_mode"] == "single" else ("audio", "visual")
        for role in roles:
            path = Path(source[f"{role}_path"])
            reason = "missing"
            if path.is_file():
                try:
                    expected = source[f"{role}_fingerprint"]
                    actual = fingerprint_source(path, sample_size=int(expected["sample_size_bytes"]))
                    if actual.digest == expected["digest"] and actual.size_bytes == expected["size_bytes"]:
                        continue
                    reason = "fingerprint_mismatch"
                except SubtitlerError:
                    reason = "unreadable"
            unresolved.append({"source_id": source["source_id"], "role": role, "path": str(path), "reason": reason})
    return unresolved


def next_incomplete_source(artifact: dict[str, Any]) -> dict[str, Any] | None:
    for source in sorted(artifact["sources"], key=lambda item: item["order"]):
        if source["status"] != "complete":
            return source
    return None


def validate_editorial_project(artifact: dict[str, Any]) -> None:
    if artifact.get("schema_version") != EDITORIAL_SCHEMA_VERSION:
        raise SubtitlerError(
            f"Unsupported editorial checkpoint schema: {artifact.get('schema_version')}"
        )
    for field in ("project_id", "title_or_game", "objective", "created_at_utc", "updated_at_utc"):
        if not isinstance(artifact.get(field), str) or not artifact[field].strip():
            raise SubtitlerError(f"Editorial checkpoint field must be non-empty: {field}")
    if artifact.get("subtitle_mode", "full") not in {"full", "emphasis"}:
        raise SubtitlerError("Editorial subtitle mode is invalid")
    if artifact.get("output_locale") not in {"en", "ja"}:
        raise SubtitlerError("Editorial output locale is invalid")
    minimum = artifact.get("target_duration_min_ms")
    maximum = artifact.get("target_duration_max_ms")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum <= 0:
        raise SubtitlerError("Editorial target minimum duration must be a positive integer")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < minimum:
        raise SubtitlerError("Editorial target maximum duration must be at least the minimum")
    pipeline_versions = artifact.get("pipeline_versions")
    if not isinstance(pipeline_versions, dict) or set(pipeline_versions) != set(EDITORIAL_PIPELINE_STAGES):
        raise SubtitlerError("Editorial checkpoint pipeline versions are invalid")
    for stage, version in pipeline_versions.items():
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            raise SubtitlerError(f"Editorial checkpoint boundary version is invalid: {stage}")
    sources = artifact.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SubtitlerError("Editorial checkpoint requires sources")
    ids: set[str] = set()
    orders: set[int] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise SubtitlerError("Editorial source records must be objects")
        source_id = source.get("source_id")
        order = source.get("order")
        if not isinstance(source_id, str) or not source_id or source_id in ids:
            raise SubtitlerError("Editorial source IDs must be non-empty and unique")
        if not isinstance(order, int) or isinstance(order, bool) or order < 0 or order in orders:
            raise SubtitlerError("Editorial source order values must be unique non-negative integers")
        ids.add(source_id)
        orders.add(order)
        mode = source.get("media_mode")
        if mode not in {"single", "paired"}:
            raise SubtitlerError(f"Editorial source media mode is invalid: {source_id}")
        if source.get("pairing_basis") not in {"single", "filename", "resolution", "manual"}:
            raise SubtitlerError(f"Editorial source pairing basis is invalid: {source_id}")
        for role in ("audio", "visual"):
            if not isinstance(source.get(f"{role}_path"), str) or not source[f"{role}_path"]:
                raise SubtitlerError(f"Editorial source {role} path is invalid: {source_id}")
            fingerprint = source.get(f"{role}_fingerprint")
            _validate_fingerprint(fingerprint, source_id)
        _validate_fingerprint(source.get("fingerprint"), source_id)
        if source.get("path") != source.get("visual_path") or source.get("fingerprint") != source.get("visual_fingerprint"):
            raise SubtitlerError(f"Editorial source compatibility fields are invalid: {source_id}")
        if mode == "single" and source["audio_path"] != source["visual_path"]:
            raise SubtitlerError(f"Single-file editorial source roles differ: {source_id}")
        if mode == "paired" and source["audio_path"] == source["visual_path"]:
            raise SubtitlerError(f"Paired editorial source roles are identical: {source_id}")
        for field in ("duration_ms", "audio_duration_ms", "visual_duration_ms"):
            if not isinstance(source.get(field), int) or isinstance(source[field], bool) or source[field] <= 0:
                raise SubtitlerError(f"Editorial source duration is invalid: {source_id}/{field}")
        if source["duration_ms"] != source["visual_duration_ms"]:
            raise SubtitlerError(f"Editorial source visual duration is inconsistent: {source_id}")
        frame_rate = source.get("frame_rate")
        if frame_rate is not None and (not isinstance(frame_rate, (int, float)) or isinstance(frame_rate, bool) or frame_rate <= 0):
            raise SubtitlerError(f"Editorial source frame rate is invalid: {source_id}")
        if mode == "paired" and frame_rate is None:
            raise SubtitlerError(f"Paired editorial source lacks a frame rate: {source_id}")
        if mode == "paired" and abs(source["audio_duration_ms"] - source["visual_duration_ms"]) > (10.0 / frame_rate) * 1000.0 + 1.0:
            raise SubtitlerError(f"Paired editorial source exceeds the 10-frame sync tolerance: {source_id}")
        stages = source.get("stages")
        if not isinstance(stages, dict) or set(stages) != set(CHECKPOINT_STAGES):
            raise SubtitlerError(f"Editorial source stages are invalid: {source_id}")
        for stage, checkpoint in stages.items():
            _validate_stage_checkpoint(checkpoint, stage, pipeline_versions[stage])
    editorial_map = artifact.get("editorial_map")
    if not isinstance(editorial_map, dict) or not isinstance(editorial_map.get("global_reconciliation"), dict):
        raise SubtitlerError("Editorial checkpoint requires a global reconciliation checkpoint")
    _validate_stage_checkpoint(
        editorial_map[GLOBAL_CHECKPOINT_STAGE],
        GLOBAL_CHECKPOINT_STAGE,
        pipeline_versions[GLOBAL_CHECKPOINT_STAGE],
    )


def _sample_offsets(size: int, sample_size: int) -> list[int]:
    if size <= sample_size * 3:
        return [0]
    last = size - sample_size
    return sorted({0, max(0, (size - sample_size) // 2), last})


def _validate_fingerprint(value: Any, source_id: str) -> None:
    if not isinstance(value, dict) or value.get("algorithm") != FINGERPRINT_ALGORITHM:
        raise SubtitlerError(f"Editorial source fingerprint is invalid: {source_id}")
    if not isinstance(value.get("digest"), str) or len(value["digest"]) != 64:
        raise SubtitlerError(f"Editorial source fingerprint digest is invalid: {source_id}")


def _validate_stage_checkpoint(value: Any, stage: str, expected_version: int) -> None:
    if not isinstance(value, dict) or value.get("version") != expected_version:
        raise SubtitlerError(f"Editorial stage boundary version is invalid: {stage}")
    if value.get("status") not in {"pending", "in_progress", "complete", "failed"}:
        raise SubtitlerError(f"Editorial stage checkpoint status is invalid: {stage}")


def _upgrade_legacy_checkpoint(artifact: dict[str, Any]) -> None:
    """Upgrade the unreleased single-file schema without re-fingerprinting media."""
    if artifact.get("schema_version") != 1:
        return
    sources = artifact.get("sources")
    if not isinstance(sources, list):
        return
    for source in sources:
        if not isinstance(source, dict):
            continue
        path = source.get("path")
        name = source.get("original_name")
        duration = source.get("duration_ms")
        fingerprint = source.get("fingerprint")
        source.update(
            {
                "media_mode": "single",
                "pairing_basis": "single",
                "audio_path": path,
                "visual_path": path,
                "audio_original_name": name,
                "visual_original_name": name,
                "audio_duration_ms": duration,
                "visual_duration_ms": duration,
                "frame_rate": None,
                "audio_fingerprint": fingerprint,
                "visual_fingerprint": fingerprint,
            }
        )
    artifact["schema_version"] = 2


def _upgrade_output_locale(artifact: dict[str, Any]) -> None:
    """Treat every checkpoint written before locale support as English."""
    if artifact.get("schema_version") != 2:
        return
    artifact["output_locale"] = "en"
    artifact["schema_version"] = EDITORIAL_SCHEMA_VERSION


def _upgrade_versionless_boundaries(artifact: dict[str, Any]) -> None:
    versions = artifact.get("pipeline_versions")
    if not isinstance(versions, dict):
        versions = dict(LEGACY_EDITORIAL_STAGE_VERSIONS)
        artifact["pipeline_versions"] = versions
    for source in artifact.get("sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("stages"), dict):
            continue
        for stage in CHECKPOINT_STAGES:
            checkpoint = source["stages"].get(stage)
            if isinstance(checkpoint, dict) and "version" not in checkpoint:
                checkpoint["version"] = int(versions.get(stage, 1))
    editorial_map = artifact.get("editorial_map")
    if isinstance(editorial_map, dict):
        checkpoint = editorial_map.get(GLOBAL_CHECKPOINT_STAGE)
        if isinstance(checkpoint, dict) and "version" not in checkpoint:
            checkpoint["version"] = int(versions.get(GLOBAL_CHECKPOINT_STAGE, 1))


def _upgrade_editorial_map_fields(artifact: dict[str, Any]) -> None:
    artifact.setdefault("subtitle_mode", "full")
    editorial_map = artifact.get("editorial_map")
    if not isinstance(editorial_map, dict):
        return
    editorial_map.setdefault("creative_suggestions", [])
    editorial_map.setdefault("emphasized_phrases", [])
    editorial_map.setdefault("timeline_coverage", [])
    editorial_map.setdefault("director_review", None)
    editorial_map.setdefault("director_model", None)
    editorial_map.setdefault("editorial_direction_summary", None)
    editorial_map.setdefault("optimal_plan", [])


def _validate_options(options: EditorialProjectOptions) -> None:
    if not options.title_or_game.strip():
        raise SubtitlerError("Editorial project title or game is required")
    if not options.objective.strip():
        raise SubtitlerError("Editorial project objective is required")
    if options.target_duration_min_ms <= 0:
        raise SubtitlerError("Editorial target minimum duration must be positive")
    if options.target_duration_max_ms < options.target_duration_min_ms:
        raise SubtitlerError("Editorial target duration range is invalid")
    if options.subtitle_mode not in {"full", "emphasis"}:
        raise SubtitlerError("Editorial subtitle mode must be full or emphasis")
    if options.output_locale not in {"en", "ja"}:
        raise SubtitlerError("Editorial output locale must be English or Japanese")


def _clean_notes(notes: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for note in notes:
        cleaned = note.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def _new_stage_checkpoint(stage: str) -> dict[str, Any]:
    return {
        "version": EDITORIAL_STAGE_VERSIONS[stage],
        "status": "pending",
        "attempts": 0,
        "started_at_utc": None,
        "completed_at_utc": None,
        "error": "",
        "output": None,
    }


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


def _source_by_id(artifact: dict[str, Any], source_id: str) -> dict[str, Any]:
    for source in artifact.get("sources", []):
        if source.get("source_id") == source_id:
            return source
    raise SubtitlerError(f"Unknown editorial source ID: {source_id}")


def _derive_source_status(stages: dict[str, dict[str, Any]]) -> str:
    statuses = {stage["status"] for stage in stages.values()}
    if statuses == {"complete"}:
        return "complete"
    if "failed" in statuses:
        return "failed"
    if "in_progress" in statuses or "complete" in statuses:
        return "in_progress"
    return "pending"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
