"""B-roll planning composition with explicit inputs and owned provider lifetime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .broll import BrollMode, BrollPlanOutcome, plan_broll
from .broll_review import request_filename_descriptions
from .broll_session import BrollSession
from .operation_store import OperationStore
from .review_exchange import ReviewStorage
from .models import ExoSettings, Subtitle


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


def run_broll_stage(request: BrollStageRequest, usage: ApiUsageLedger) -> BrollPlanOutcome:
    print("Planning B-roll from the indexed Media Library...", flush=True)
    store = OperationStore(request.revision_directory / "broll-operations", request.input_revision, usage) if request.revision_directory else None
    provider = BrollSession(request.config, usage, store)
    try:
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
