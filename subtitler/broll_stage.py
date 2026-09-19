"""B-roll planning composition with explicit inputs and owned provider lifetime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .broll import BrollMode, BrollPlanOutcome, plan_broll
from .broll_review import request_filename_descriptions, request_placement_choices
from .broll_verification import verify_placement
from .visual_evidence_store import load_visual_evidence
from .broll_session import BrollSession
from .operation_store import OperationStore
from .review_exchange import ReviewStorage
from .models import ExoSettings, Subtitle
from .silence_cut import TimelineMap


@dataclass(frozen=True)
class BrollStageRequest:
    mode: BrollMode
    config: dict[str, Any]
    database_path: Path | None
    subtitles: list[Subtitle]
    settings: ExoSettings
    frontend_protocol: str | None = None
    sidecar_path: Path | None = None
    revision_directory: Path | None = None
    input_revision: str = ""
    source_path: Path | None = None
    source_cuts: tuple[tuple[float, float], ...] = ()


def run_broll_stage(request: BrollStageRequest, usage: ApiUsageLedger) -> BrollPlanOutcome:
    print("Planning B-roll from the indexed Media Library...", flush=True)
    store = OperationStore(request.revision_directory / "broll-operations", request.input_revision, usage) if request.revision_directory else None
    provider = BrollSession(request.config, usage, store)
    try:
        evidence = load_visual_evidence(request.source_path) if request.source_path else None
        broll_outcome = plan_broll(
            mode=request.mode,
            database_path=request.database_path,
            subtitles=request.subtitles,
            fps=request.settings.rate,
            canvas_width=request.settings.width,
            canvas_height=request.settings.height,
            provider=provider,
            sidecar_path=request.sidecar_path,
            catalog=provider.catalog(request.database_path),
            source_scenes=retime_source_scenes([asdict(scene) for scene in evidence.segments], request.source_cuts) if evidence else [],
            verify=lambda item, subtitles: verify_placement(provider, item, subtitles),
            choose=(lambda candidates, subtitles: request_placement_choices(
                candidates, subtitles, request.frontend_protocol,
                ReviewStorage(request.revision_directory / "broll-placement-review.json", request.input_revision)
                if request.revision_directory else None,
            )) if request.frontend_protocol == "stdio-v1" else None,
            review_catalog=lambda: provider.catalog(request.database_path, operation="broll_review_catalog"),
            review=lambda candidates, subtitles: request_filename_descriptions(
                candidates, subtitles, request.frontend_protocol,
                ReviewStorage(request.revision_directory / "broll-review.json", request.input_revision)
                if request.revision_directory else None,
            ),
            web_discovery=provider.discover if request.config["broll"].get("discover_web_assets", True) else None,
        )
    finally:
        provider.close()
    print(
        f"B-roll candidates: {broll_outcome.editorial_need_count} editorial need(s), "
        f"{broll_outcome.retrieved_asset_count}/{broll_outcome.catalog_asset_count} catalog asset(s) "
        f"retrieved, {broll_outcome.filename_review_count} filename-only match(es) reviewed "
        f"({broll_outcome.filename_described_count} described, "
        f"{broll_outcome.filename_rejected_count} rejected), "
        f"{len(broll_outcome.proposed)} placement(s) proposed, "
        f"{broll_outcome.planner_rejection_count} malformed/invalid proposal(s), "
        f"{broll_outcome.safety_omission_count} safety-filtered.",
        flush=True,
    )
    print(
        f"B-roll result: {len(broll_outcome.placements)} placement(s), "
        f"{len(broll_outcome.missing_assets)} unmet asset need(s), "
        f"{len(broll_outcome.web_candidates)} web candidate(s).",
        flush=True,
    )

    return broll_outcome


def broll_summary(outcome: BrollPlanOutcome, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "provider": outcome.provider,
        "model": outcome.model,
        "proposed_count": len(outcome.proposed),
        "placement_count": len(outcome.placements),
        "editorial_need_count": outcome.editorial_need_count,
        "catalog_asset_count": outcome.catalog_asset_count,
        "retrieved_asset_count": outcome.retrieved_asset_count,
        "filename_review_count": outcome.filename_review_count,
        "filename_described_count": outcome.filename_described_count,
        "filename_rejected_count": outcome.filename_rejected_count,
        "planner_rejection_count": outcome.planner_rejection_count,
        "safety_omission_count": outcome.safety_omission_count,
        "missing_asset_count": len(outcome.missing_assets),
        "web_candidate_count": len(outcome.web_candidates),
        "omitted_count": len(outcome.omitted),
        "error": outcome.error,
    }


def retime_source_scenes(scenes: list[dict[str, Any]], cuts: tuple[tuple[float, float], ...]) -> list[dict[str, Any]]:
    timeline = TimelineMap(cuts)
    retimed = []
    for scene in scenes:
        start = round(timeline.map_time(float(scene["start_ms"]) / 1000) * 1000)
        end = round(timeline.map_time(float(scene["end_ms"]) / 1000) * 1000)
        if end > start:
            retimed.append({**scene, "start_ms": start, "end_ms": end})
    return retimed
