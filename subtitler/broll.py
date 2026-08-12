"""Transcript-aware B-roll planning against the managed media catalog."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, Sequence

from .api_usage import ApiUsageLedger
from .external_refiners import GeminiTextRefiner, HostedTextRefiner, OpenAITextRefiner
from .models import BrollPlacement, Subtitle
from .silence_cut import emit_frontend_event
from .web_assets import WebAssetCandidate


MIN_RELEVANCE_SCORE = 0.72
MIN_PLACEMENT_SAFETY_SCORE = 0.80
MIN_SOURCE_GROUNDING_SCORE = 0.65
MIN_TECHNICAL_QUALITY_SCORE = 0.35
MIN_OVERALL_CONFIDENCE = 0.68
MAX_CATALOG_POOL = 1000
MAX_RETRIEVED_ASSETS = 120
PER_NEED_KIND_LIMIT = 6
MAX_TRANSCRIPT_CHARS = 60_000
BrollMode = Literal["off", "automatic"]
DescriptionSource = Literal["user", "ai", "inferred"]
DisplayMode = Literal["cover", "contain", "overlay"]


class BrollPlanningProvider(Protocol):
    provider: str
    model: str

    def complete(self, prompt: str) -> str: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class CatalogSegment:
    id: str
    start_sec: float
    end_sec: float
    description: str
    confidence: float
    tags: tuple[str, ...] = ()
    motion_level: float | None = None
    visual_category: str = ""
    suitability: str = ""
    description_source: Literal["user", "ai"] = "ai"
    locked: bool = False


@dataclass(frozen=True)
class CatalogAsset:
    id: str
    path: Path
    media_kind: Literal["video", "image"]
    title: str
    description: str
    duration_sec: float | None
    has_audio: bool
    segments: tuple[CatalogSegment, ...]
    source_fps: float | None = None
    description_source: DescriptionSource = "user"
    tags: tuple[str, ...] = ()
    width: int | None = None
    height: int | None = None
    transparency: str = "unsupported"
    analysis_state: str = "metadata_only"


@dataclass(frozen=True)
class BrollNeed:
    start_line: int
    end_line: int
    description: str
    search_terms: tuple[str, ...]
    preferred_media: Literal["video", "image", "either"]
    need_score: float
    reason: str = ""


@dataclass(frozen=True)
class ProposedPlacement:
    id: str
    asset: CatalogAsset
    start_line: int
    end_line: int
    source_start_sec: float
    source_end_sec: float | None
    confidence: float
    reason: str
    need_score: float = 0.0
    relevance_score: float = 0.0
    placement_safety_score: float = 0.0
    source_grounding_score: float = 0.0
    technical_quality_score: float = 0.0
    display_mode: DisplayMode = "cover"
    display_intent_score: float = 0.0


@dataclass(frozen=True)
class FilenameReviewCandidate:
    id: str
    asset: CatalogAsset
    start_line: int
    end_line: int
    source_start_sec: float
    source_end_sec: float | None
    confidence: float
    reason: str


@dataclass(frozen=True)
class MissingAssetNeed:
    start_line: int
    end_line: int
    description: str
    reason: str


@dataclass(frozen=True)
class BrollPlanOutcome:
    placements: list[BrollPlacement]
    proposed: list[ProposedPlacement]
    missing_assets: list[MissingAssetNeed]
    omitted: list[dict[str, Any]]
    provider: str
    model: str
    web_candidates: list[WebAssetCandidate]
    error: str = ""
    editorial_need_count: int = 0
    catalog_asset_count: int = 0
    retrieved_asset_count: int = 0
    filename_review_count: int = 0
    filename_described_count: int = 0
    filename_rejected_count: int = 0
    planner_rejection_count: int = 0
    safety_omission_count: int = 0


class HostedBrollProvider:
    def __init__(self, refiner: HostedTextRefiner) -> None:
        self.refiner = refiner
        self.provider = refiner.provider
        self.model = refiner.model

    def complete(self, prompt: str) -> str:
        return self.refiner.complete_structured(prompt, max_tokens=4096, operation="broll_planning")

    def close(self) -> None:
        self.refiner.close()


def build_broll_provider(config: dict[str, Any], usage: ApiUsageLedger) -> BrollPlanningProvider:
    cleanup = config["cleanup"]
    backend = cleanup["backend"]
    if backend == "openai":
        return HostedBrollProvider(
            OpenAITextRefiner(
                model=str(cleanup["api_model"]),
                glossary=[],
                usage=usage,
                reasoning_effort=cleanup.get("reasoning_effort"),
            )
        )
    if backend == "gemini":
        return HostedBrollProvider(
            GeminiTextRefiner(
                model=str(cleanup["api_model"]),
                glossary=[],
                usage=usage,
                thinking_level=cleanup.get("thinking_level"),
            )
        )
    raise ValueError("B-roll planning requires a hosted cleanup provider")


def load_catalog(database_path: Path, transcript_text: str = "") -> list[CatalogAsset]:
    """Read a bounded, enabled and currently available catalog snapshot."""
    del transcript_text  # Retrieval happens per editorial need after the transcript pass.
    if not database_path.is_file():
        return []
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as db:
        db.row_factory = sqlite3.Row
        asset_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(assets)")}
        segment_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(asset_segments)")}
        has_visibility = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_directory_visibility'"
        ).fetchone()
        visibility_clause = (
            """
            AND NOT EXISTS (
              SELECT 1 FROM library_directory_visibility v
              WHERE v.root_id=a.root_id AND v.visible=0 AND (
                (v.kind='direct' AND v.relative_directory=a.relative_directory)
                OR (
                  v.kind='subtree' AND (
                    v.relative_directory=''
                    OR a.relative_directory=v.relative_directory
                    OR a.relative_directory LIKE v.relative_directory || '\\%'
                  )
                )
              )
            )
            """
            if has_visibility
            else ""
        )
        optional_asset_fields = ", ".join(
            [
                _select_column(asset_columns, "tags_json", "'[]'"),
                _select_column(asset_columns, "width", "NULL"),
                _select_column(asset_columns, "height", "NULL"),
                _select_column(asset_columns, "transparency", "'unsupported'"),
                _select_column(asset_columns, "analysis_state", "'metadata_only'"),
            ]
        )
        rows = db.execute(
            f"""
            SELECT a.id, a.canonical_path, a.media_kind, a.title,
                   COALESCE(NULLIF(a.user_description, ''),
                            NULLIF(a.ai_description, ''),
                            a.inferred_description, '') AS description,
                   CASE WHEN a.user_description<>'' THEN 'user'
                        WHEN a.ai_description<>'' THEN 'ai' ELSE 'inferred' END AS description_source,
                   a.duration_ms, a.frame_rate_num, a.frame_rate_den, a.has_audio,
                   {optional_asset_fields}
            FROM assets a
            JOIN library_roots r ON r.id=a.root_id
            WHERE r.enabled=1 AND a.availability='active'
            {visibility_clause}
            ORDER BY CASE WHEN a.user_description<>'' THEN 0
                          WHEN a.ai_description<>'' THEN 1 ELSE 2 END,
                     a.updated_at DESC
            LIMIT {MAX_CATALOG_POOL}
            """
        ).fetchall()
        assets: list[CatalogAsset] = []
        optional_segment_fields = ", ".join(
            [
                _select_column(segment_columns, "tags_json", "'[]'", table_alias=""),
                _select_column(segment_columns, "motion_level", "NULL", table_alias=""),
                _select_column(segment_columns, "visual_category", "''", table_alias=""),
                _select_column(segment_columns, "suitability", "''", table_alias=""),
                _select_column(segment_columns, "origin", "'ai'", table_alias=""),
                _select_column(segment_columns, "locked", "0", table_alias=""),
            ]
        )
        for row in rows:
            asset_path = Path(str(row["canonical_path"]))
            if not asset_path.is_file():
                continue
            segment_rows = db.execute(
                f"""
                SELECT id, start_ms, end_ms, description, confidence, {optional_segment_fields}
                FROM asset_segments
                WHERE asset_id=? AND end_ms>start_ms
                ORDER BY start_ms, end_ms
                LIMIT 300
                """,
                (row["id"],),
            ).fetchall()
            segments = tuple(
                CatalogSegment(
                    id=str(item["id"]),
                    start_sec=float(item["start_ms"]) / 1000.0,
                    end_sec=float(item["end_ms"]) / 1000.0,
                    description=str(item["description"] or ""),
                    confidence=_confidence(item["confidence"]),
                    tags=_json_tags(item["tags_json"]),
                    motion_level=_optional_float(item["motion_level"]),
                    visual_category=str(item["visual_category"] or ""),
                    suitability=str(item["suitability"] or ""),
                    description_source=str(item["origin"] or "ai"),  # type: ignore[arg-type]
                    locked=bool(item["locked"]),
                )
                for item in segment_rows
            )
            duration_ms = row["duration_ms"]
            frame_rate_num = row["frame_rate_num"]
            frame_rate_den = row["frame_rate_den"]
            source_fps = None
            if frame_rate_num is not None and frame_rate_den not in (None, 0):
                source_fps = float(frame_rate_num) / float(frame_rate_den)
            assets.append(
                CatalogAsset(
                    id=str(row["id"]),
                    path=asset_path,
                    media_kind=str(row["media_kind"]),  # type: ignore[arg-type]
                    title=str(row["title"] or asset_path.stem),
                    description=str(row["description"] or ""),
                    duration_sec=float(duration_ms) / 1000.0 if duration_ms is not None else None,
                    has_audio=bool(row["has_audio"]),
                    segments=segments,
                    source_fps=source_fps,
                    description_source=str(row["description_source"]),  # type: ignore[arg-type]
                    tags=_json_tags(row["tags_json"]),
                    width=_optional_int(row["width"]),
                    height=_optional_int(row["height"]),
                    transparency=str(row["transparency"] or "unsupported"),
                    analysis_state=str(row["analysis_state"] or "metadata_only"),
                )
            )
        return assets


def plan_broll(
    *,
    mode: BrollMode,
    database_path: Path | None,
    subtitles: Sequence[Subtitle],
    fps: int,
    canvas_width: int = 2560,
    canvas_height: int = 1440,
    provider: BrollPlanningProvider,
    frontend_protocol: str | None,
    sidecar_path: Path | None,
    web_discovery: Callable[[Sequence[MissingAssetNeed]], list[WebAssetCandidate]] | None = None,
) -> BrollPlanOutcome:
    assets = load_catalog(database_path) if database_path is not None else []
    if not assets:
        print("Warning: B-roll is enabled, but the Media Library has no available indexed assets.", flush=True)

    try:
        needs_raw = provider.complete(_needs_prompt(subtitles))
        needs, protected_ranges = parse_broll_needs(needs_raw, subtitles)
        retrieved = retrieve_catalog_assets(assets, needs)
        raw = provider.complete(_planning_prompt(subtitles, retrieved, needs, protected_ranges))
        review_candidates = parse_filename_review_candidates(
            raw,
            retrieved,
            subtitles,
            protected_ranges=protected_ranges,
        )
        proposed, missing_assets, rejected = parse_broll_response(
            raw,
            retrieved,
            subtitles,
            protected_ranges=protected_ranges,
        )
        planner_rejection_count = len(rejected)
        described_count = 0
        rejected_description_count = 0

        if review_candidates:
            descriptions, library_candidate_ids = request_filename_descriptions(
                review_candidates,
                subtitles,
                frontend_protocol,
            )
            accepted_candidate_ids = {*descriptions, *library_candidate_ids}
            rejected_ids = {item.id for item in review_candidates if item.id not in accepted_candidate_ids}
            described_count = len(review_candidates) - len(rejected_ids)
            rejected_description_count = len(rejected_ids)
            rejected.extend(
                {
                    "id": item.id,
                    "asset_id": item.asset.id,
                    "reason": "filename_only_candidate_rejected",
                }
                for item in review_candidates
                if item.id in rejected_ids
            )
            enriched_by_id: dict[str, CatalogAsset] = {}
            refreshed_by_id = {
                asset.id: asset
                for asset in load_catalog(database_path)
            } if database_path is not None and library_candidate_ids else {}
            for item in review_candidates:
                description = descriptions.get(item.id)
                if description:
                    enriched_by_id[item.asset.id] = replace(
                        item.asset,
                        description=description,
                        description_source="user",
                    )
                elif item.id in library_candidate_ids and item.asset.id in refreshed_by_id:
                    enriched_by_id[item.asset.id] = refreshed_by_id[item.asset.id]
            final_assets = [
                enriched_by_id.get(asset.id, asset)
                for asset in retrieved
                if asset.description_source != "inferred" or asset.id in enriched_by_id
            ]
            final_raw = provider.complete(
                _planning_prompt(
                    subtitles,
                    final_assets,
                    needs,
                    protected_ranges,
                )
            )
            proposed, missing_assets, final_rejected = parse_broll_response(
                final_raw,
                final_assets,
                subtitles,
                protected_ranges=protected_ranges,
            )
            rejected.extend(final_rejected)
            planner_rejection_count += len(final_rejected)

        accepted, safety_omitted = apply_confidence_policy(
            proposed,
            mode=mode,
            frontend_protocol=frontend_protocol,
        )
        omitted = [*rejected, *safety_omitted]
        placements = [
            _to_exo_placement(item, subtitles, fps, canvas_width, canvas_height)
            for item in accepted
        ]
        web_candidates: list[WebAssetCandidate] = []
        if web_discovery and missing_assets:
            try:
                web_candidates = web_discovery(missing_assets)
            except Exception as exc:
                print(f"Warning: B-roll web discovery failed; keeping local placements. {exc}", flush=True)
        outcome = BrollPlanOutcome(
            placements=placements,
            proposed=proposed,
            missing_assets=missing_assets,
            omitted=omitted,
            provider=provider.provider,
            model=provider.model,
            web_candidates=web_candidates,
            editorial_need_count=len(needs),
            catalog_asset_count=len(assets),
            retrieved_asset_count=len(retrieved),
            filename_review_count=len(review_candidates),
            filename_described_count=described_count,
            filename_rejected_count=rejected_description_count,
            planner_rejection_count=planner_rejection_count,
            safety_omission_count=len(safety_omitted),
        )
    except Exception as exc:
        outcome = BrollPlanOutcome(
            placements=[],
            proposed=[],
            missing_assets=[],
            omitted=[],
            provider=provider.provider,
            model=provider.model,
            web_candidates=[],
            error=str(exc),
        )
        print(f"Warning: B-roll planning failed; continuing without B-roll. {exc}", flush=True)
    _write_plan(sidecar_path, mode, outcome)
    return outcome


def parse_broll_needs(
    raw: str,
    subtitles: Sequence[Subtitle],
) -> tuple[list[BrollNeed], list[tuple[int, int]]]:
    data = _json_object(raw)
    protected = _line_ranges(data.get("protected_ranges"), len(subtitles))
    needs: list[BrollNeed] = []
    raw_needs = data.get("needs")
    if not isinstance(raw_needs, list):
        return needs, protected
    for value in raw_needs[:100]:
        if not isinstance(value, dict):
            continue
        try:
            start_line = int(value.get("start_line"))
            end_line = int(value.get("end_line"))
        except (TypeError, ValueError):
            continue
        description = str(value.get("description") or "").strip()
        preferred = str(value.get("preferred_media") or "either").strip().lower()
        if (
            not 1 <= start_line <= end_line <= len(subtitles)
            or not description
            or preferred not in {"video", "image", "either"}
            or _overlaps_ranges(start_line, end_line, protected)
        ):
            continue
        search_terms = _string_list(value.get("search_terms"), 20)
        if not search_terms:
            search_terms = tuple(_search_tokens(description))
        needs.append(
            BrollNeed(
                start_line=start_line,
                end_line=end_line,
                description=description[:2000],
                search_terms=search_terms,
                preferred_media=preferred,  # type: ignore[arg-type]
                need_score=_confidence(value.get("need_score")),
                reason=str(value.get("reason") or "").strip()[:1000],
            )
        )
    return needs, protected


def retrieve_catalog_assets(assets: Sequence[CatalogAsset], needs: Sequence[BrollNeed]) -> list[CatalogAsset]:
    """Retrieve a small, media-balanced catalog for each editorial need."""
    selected: dict[str, CatalogAsset] = {}
    for need in needs:
        for media_kind in ("video", "image"):
            if need.preferred_media != "either" and need.preferred_media != media_kind:
                continue
            ranked = sorted(
                (
                    (_asset_need_score(asset, need), asset)
                    for asset in assets
                    if asset.media_kind == media_kind
                ),
                key=lambda item: (item[0], item[1].description_source != "inferred", item[1].analysis_state == "ready"),
                reverse=True,
            )
            for score, asset in ranked[:PER_NEED_KIND_LIMIT]:
                if score <= 0:
                    continue
                selected.setdefault(asset.id, asset)
                if len(selected) >= MAX_RETRIEVED_ASSETS:
                    return list(selected.values())
    return list(selected.values())


def parse_filename_review_candidates(
    raw: str,
    assets: Sequence[CatalogAsset],
    subtitles: Sequence[Subtitle],
    *,
    protected_ranges: Sequence[tuple[int, int]] = (),
) -> list[FilenameReviewCandidate]:
    """Extract title-only matches that need human description before placement planning."""
    data = _json_object(raw)
    by_asset = {asset.id: asset for asset in assets}
    values = data.get("filename_review_candidates")
    raw_candidates = list(values) if isinstance(values, list) else []

    # Be tolerant of a provider using the old schema: a direct placement of an
    # inferred asset is converted into a review candidate instead of disappearing.
    raw_placements = data.get("placements")
    if isinstance(raw_placements, list):
        for value in raw_placements:
            if not isinstance(value, dict):
                continue
            asset = by_asset.get(str(value.get("asset_id") or ""))
            segment_id = str(value.get("segment_id") or "")
            has_described_segment = any(segment.id == segment_id for segment in asset.segments) if asset else False
            if asset is not None and asset.description_source == "inferred" and not has_described_segment:
                raw_candidates.append(value)

    candidates: list[FilenameReviewCandidate] = []
    seen_assets: set[str] = set()
    for value in raw_candidates[:100]:
        if not isinstance(value, dict):
            continue
        asset = by_asset.get(str(value.get("asset_id") or ""))
        if asset is None or asset.description_source != "inferred" or asset.id in seen_assets:
            continue
        try:
            start_line = int(value.get("start_line"))
            end_line = int(value.get("end_line"))
        except (TypeError, ValueError):
            continue
        if (
            not 1 <= start_line <= end_line <= len(subtitles)
            or _overlaps_ranges(start_line, end_line, protected_ranges)
        ):
            continue
        confidence = _confidence(value.get("confidence", value.get("relevance_score")))
        candidates.append(
            FilenameReviewCandidate(
                id=f"broll-review-{len(candidates) + 1:04d}",
                asset=asset,
                start_line=start_line,
                end_line=end_line,
                source_start_sec=0.0,
                source_end_sec=asset.duration_sec if asset.media_kind == "video" else None,
                confidence=confidence,
                reason=str(value.get("reason") or "The title may match this editorial need.").strip()[:1000],
            )
        )
        seen_assets.add(asset.id)
    return candidates


def parse_broll_response(
    raw: str,
    assets: Sequence[CatalogAsset],
    subtitles: Sequence[Subtitle],
    *,
    protected_ranges: Sequence[tuple[int, int]] = (),
    confirmed_ranges: dict[str, list[tuple[float, float | None]]] | None = None,
) -> tuple[list[ProposedPlacement], list[MissingAssetNeed], list[dict[str, Any]]]:
    data = _json_object(raw)
    by_asset = {asset.id: asset for asset in assets}
    confirmed = confirmed_ranges or {}
    proposed: list[ProposedPlacement] = []
    rejected: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    raw_placements = data.get("placements")
    if not isinstance(raw_placements, list):
        raw_placements = []
    for index, value in enumerate(raw_placements):
        try:
            if not isinstance(value, dict):
                raise ValueError("invalid placement")
            asset = by_asset.get(str(value.get("asset_id") or ""))
            if asset is None:
                raise ValueError("unknown asset")
            start_line = int(value.get("start_line"))
            end_line = int(value.get("end_line"))
            if not 1 <= start_line <= end_line <= len(subtitles):
                raise ValueError("line range is outside the transcript")
            if _overlaps_ranges(start_line, end_line, protected_ranges):
                raise ValueError("placement overlaps protected on-screen explanation")
            if any(start_line <= used_end and end_line >= used_start for used_start, used_end in occupied):
                raise ValueError("placement overlaps another placement")
            source_start, source_end, segment = _validated_source_range(value, asset)
            if asset.description_source == "inferred" and segment is None:
                raise ValueError("filename-only asset requires user review before placement")
            if asset.id in confirmed and not _range_confirmed(source_start, source_end, confirmed[asset.id]):
                raise ValueError("source range was not confirmed by the user")
            fallback = _confidence(value.get("confidence"))
            need_score = _score(value, "need_score", fallback)
            relevance_score = _score(value, "relevance_score", fallback)
            safety_score = _score(value, "placement_safety_score", fallback)
            grounding_score = _grounding_score(value, asset, segment, confirmed)
            technical_score = _technical_quality_score(value, asset)
            confidence = min(need_score, relevance_score, safety_score, grounding_score)
            display_mode = _display_mode(value)
            proposed.append(
                ProposedPlacement(
                    id=f"broll-{index + 1:04d}",
                    asset=asset,
                    start_line=start_line,
                    end_line=end_line,
                    source_start_sec=source_start,
                    source_end_sec=source_end,
                    confidence=confidence,
                    reason=str(value.get("reason") or "").strip()[:1000],
                    need_score=need_score,
                    relevance_score=relevance_score,
                    placement_safety_score=safety_score,
                    source_grounding_score=grounding_score,
                    technical_quality_score=technical_score,
                    display_mode=display_mode,
                    display_intent_score=_confidence(value.get("display_intent_score")),
                )
            )
            occupied.append((start_line, end_line))
        except (TypeError, ValueError) as exc:
            rejected.append({"index": index, "reason": str(exc), "value": value})

    missing: list[MissingAssetNeed] = []
    raw_missing = data.get("missing_assets")
    if isinstance(raw_missing, list):
        for value in raw_missing[:100]:
            if not isinstance(value, dict):
                continue
            try:
                start_line = int(value.get("start_line"))
                end_line = int(value.get("end_line"))
            except (TypeError, ValueError):
                continue
            description = str(value.get("description") or "").strip()
            if 1 <= start_line <= end_line <= len(subtitles) and description:
                missing.append(
                    MissingAssetNeed(
                        start_line,
                        end_line,
                        description[:2000],
                        str(value.get("reason") or "").strip()[:1000],
                    )
                )
    return proposed, missing, rejected


def apply_confidence_policy(
    proposed: Sequence[ProposedPlacement],
    *,
    mode: BrollMode,
    frontend_protocol: str | None,
) -> tuple[list[ProposedPlacement], list[dict[str, Any]]]:
    del mode, frontend_protocol
    accepted: list[ProposedPlacement] = []
    omitted: list[dict[str, Any]] = []
    for item in proposed:
        failed: list[str] = []
        if item.relevance_score < MIN_RELEVANCE_SCORE:
            failed.append("relevance")
        if item.placement_safety_score < MIN_PLACEMENT_SAFETY_SCORE:
            failed.append("placement_safety")
        if item.source_grounding_score < MIN_SOURCE_GROUNDING_SCORE:
            failed.append("source_grounding")
        if item.technical_quality_score < MIN_TECHNICAL_QUALITY_SCORE:
            failed.append("technical_quality")
        if item.confidence < MIN_OVERALL_CONFIDENCE:
            failed.append("overall_confidence")
        if item.display_mode == "overlay" and item.display_intent_score < 0.90:
            failed.append("overlay_intent")
        if failed:
            omitted.append(
                {
                    "id": item.id,
                    "reason": "safe_policy_failed",
                    "failed_scores": failed,
                    "confidence": item.confidence,
                }
            )
        else:
            accepted.append(item)
    accepted.sort(key=lambda item: (item.start_line, item.end_line))
    return accepted, omitted


def request_filename_descriptions(
    candidates: Sequence[FilenameReviewCandidate],
    subtitles: Sequence[Subtitle],
    frontend_protocol: str | None,
) -> tuple[dict[str, str], set[str]]:
    """Ask the user to validate and describe title-only candidates before final planning."""
    if frontend_protocol != "stdio-v1":
        return {}, set()
    review_id = str(uuid.uuid4())
    emit_frontend_event(
        "broll-review-required",
        reviewId=review_id,
        candidates=[
            {
                "id": item.id,
                "assetId": item.asset.id,
                "assetPath": str(item.asset.path),
                "title": item.asset.title,
                "mediaKind": item.asset.media_kind,
                "startLine": item.start_line,
                "endLine": item.end_line,
                "transcriptText": " ".join(
                    subtitle.text for subtitle in subtitles[item.start_line - 1:item.end_line]
                )[:4000],
                "sourceStartSec": item.source_start_sec,
                "sourceEndSec": item.source_end_sec,
                "confidence": item.confidence,
                "reason": item.reason,
                "descriptionRequired": True,
            }
            for item in candidates
        ],
    )
    line = sys.stdin.readline()
    if not line:
        return {}, set()
    try:
        response = json.loads(line)
    except json.JSONDecodeError:
        return {}, set()
    if (
        not isinstance(response, dict)
        or response.get("type") != "broll-review-result"
        or response.get("reviewId") != review_id
    ):
        return {}, set()
    valid_ids = {item.id for item in candidates}
    descriptions: dict[str, str] = {}
    library_candidates: set[str] = set()
    seen: set[str] = set()
    for item in response.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidateId") or "")
        decision = str(item.get("decision") or "")
        description = str(item.get("description") or "").strip()[:4000]
        if candidate_id not in valid_ids or candidate_id in seen:
            continue
        seen.add(candidate_id)
        if decision == "describe" and description:
            descriptions[candidate_id] = description
        elif decision == "use_library":
            library_candidates.add(candidate_id)
    return descriptions, library_candidates


def _needs_prompt(subtitles: Sequence[Subtitle]) -> str:
    return (
        "Act as a conservative video editor. Identify only transcript passages where optional B-roll would "
        "materially help, and separately protect passages where the speaker probably expects the viewer to "
        "look at the primary video. Infer protected passages from wording such as 'as you can see', 'on screen', "
        "'here', 'this button/menu/chart', clicking, opening, scrolling, selecting, pointing, comparing visible "
        "items, step-by-step demonstrations, and equivalent cues in any language (for Japanese, examples include "
        "画面, ご覧, ここ, こちら, このボタン, クリック, 選択, 表示, 見て). Protect the complete explanation, "
        "not just the cue sentence. Never create a B-roll need inside a protected range. Do not seek a coverage "
        "quota; zero needs is valid. Give concrete multilingual search terms, including useful English aliases "
        "for Japanese topics and exact product/game/person names. Return strict JSON only as "
        '{"needs":[{"start_line":1,"end_line":2,"description":"...","search_terms":["..."],'
        '"preferred_media":"video|image|either","need_score":0.0,"reason":"..."}],'
        '"protected_ranges":[{"start_line":3,"end_line":5,"reason":"the viewer is being shown a menu"}]}.'
        "\n\nTRANSCRIPT (line, start, end, text):\n"
        + _transcript_lines(subtitles)
    )


def _planning_prompt(
    subtitles: Sequence[Subtitle],
    assets: Sequence[CatalogAsset],
    needs: Sequence[BrollNeed] = (),
    protected_ranges: Sequence[tuple[int, int]] = (),
    *,
    confirmed_ranges: dict[str, list[tuple[float, float | None]]] | None = None,
) -> str:
    catalog_lines = []
    confirmed = confirmed_ranges or {}
    for asset in assets:
        ranges = [
            {
                "segment_id": segment.id,
                "start_sec": round(segment.start_sec, 3),
                "end_sec": round(segment.end_sec, 3),
                "description": segment.description[:500],
                "tags": segment.tags,
                "confidence": segment.confidence,
                "motion_level": segment.motion_level,
                "visual_category": segment.visual_category,
                "suitability": segment.suitability[:500],
                "description_source": segment.description_source,
                "user_locked": segment.locked,
            }
            for segment in asset.segments
        ]
        catalog_lines.append(
            json.dumps(
                {
                    "asset_id": asset.id,
                    "kind": asset.media_kind,
                    "title": asset.title,
                    "description": asset.description[:1000],
                    "description_quality": "filename_only" if asset.description_source == "inferred" else "detailed",
                    "analysis_state": asset.analysis_state,
                    "tags": asset.tags,
                    "duration_sec": asset.duration_sec,
                    "width": asset.width,
                    "height": asset.height,
                    "transparency": asset.transparency,
                    "segments": ranges,
                    "user_confirmed_ranges": confirmed.get(asset.id, []),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    need_lines = [json.dumps(asdict(need), ensure_ascii=False, separators=(",", ":")) for need in needs]
    protected_text = json.dumps(list(protected_ranges), separators=(",", ":"))
    return (
        "Plan optional B-roll using only the retrieved catalog and the supplied editorial needs. The primary "
        "video may be a screen recording or demonstration, not a talking head. Never place B-roll over a "
        "protected range or over wording that implies the viewer should inspect the current screen, even if the "
        "protected list missed it. It is better to omit a weak match. Do not target a coverage quota. Keep density "
        "natural: avoid back-to-back or repetitive placements, but do not impose a rigid count. Use images for "
        "logos, screenshots, diagrams, documents, maps, or artwork when motion adds no value. Do not reuse an asset "
        "unless clearly justified. A filename-only asset may be placed only by selecting one of its described "
        "segments. Never place an unsegmented portion of a filename-only asset. If its title is a "
        "plausible, specific match for an editorial need, put it in filename_review_candidates so the user can "
        "inspect and describe it first. Select no more than the single strongest filename-only candidate for each "
        "need, and do not select generic or incidental word matches. A title-only candidate does not need to pass "
        "the final placement confidence thresholds yet. "
        "For videos prefer an analyzed segment; otherwise choose a precise range and lower source_grounding_score. "
        "Use only user_confirmed_ranges when present. Default display_mode is cover. Use contain when cropping would "
        "remove important diagram/UI/document content. Use overlay only for an intentional small comedic/reaction "
        "insert, and give it display_intent_score >= 0.9 only when that intent is explicit. Score each independent "
        "dimension from 0 to 1: need_score, relevance_score, placement_safety_score, source_grounding_score, "
        "technical_quality_score, and display_intent_score. Return strict JSON only with this shape:\n"
        '{"placements":[{"start_line":1,"end_line":2,"asset_id":"id","segment_id":null,'
        '"source_start_sec":0,"source_end_sec":5,"need_score":0.8,"relevance_score":0.9,'
        '"placement_safety_score":0.9,"source_grounding_score":0.7,"technical_quality_score":0.8,'
        '"display_mode":"cover|contain|overlay","display_intent_score":0.0,"reason":"..."}],'
        '"filename_review_candidates":[{"start_line":3,"end_line":4,"asset_id":"id",'
        '"confidence":0.7,"reason":"the specific title appears to match this need"}],'
        '"missing_assets":[{"start_line":3,"end_line":4,"description":"...","reason":"..."}]}\n\n'
        "PROTECTED LINE RANGES:\n"
        + protected_text
        + "\n\nEDITORIAL NEEDS (one JSON object per line):\n"
        + "\n".join(need_lines)
        + "\n\nTRANSCRIPT (line, start, end, text):\n"
        + _transcript_lines(subtitles)
        + "\n\nRETRIEVED CATALOG (one JSON object per line):\n"
        + "\n".join(catalog_lines)
    )


def _validated_source_range(
    value: dict[str, Any], asset: CatalogAsset
) -> tuple[float, float | None, CatalogSegment | None]:
    segment_id = str(value.get("segment_id") or "")
    segment = next((item for item in asset.segments if item.id == segment_id), None)
    if segment is not None:
        return segment.start_sec, segment.end_sec, segment
    if asset.media_kind == "image":
        return 0.0, None, None
    source_start = _finite_non_negative(value.get("source_start_sec"), "invalid source start")
    source_end = _finite_non_negative(value.get("source_end_sec"), "invalid source end")
    if source_end <= source_start:
        raise ValueError("source range is empty")
    if asset.duration_sec is not None and source_end > asset.duration_sec + 0.05:
        raise ValueError("source range exceeds asset duration")
    return source_start, source_end, None


def _to_exo_placement(
    item: ProposedPlacement,
    subtitles: Sequence[Subtitle],
    fps: int,
    canvas_width: int = 2560,
    canvas_height: int = 1440,
) -> BrollPlacement:
    start = subtitles[item.start_line - 1].start_time
    requested_end = subtitles[item.end_line - 1].end_time
    end = requested_end
    if item.asset.media_kind == "video" and item.source_end_sec is not None:
        end = min(requested_end, start + (item.source_end_sec - item.source_start_sec))
    start_frame = int(start * fps) + 1
    end_frame = max(start_frame, int(end * fps) + 1)
    return BrollPlacement(
        id=item.id,
        asset_id=item.asset.id,
        asset_path=item.asset.path,
        media_kind=item.asset.media_kind,
        output_start_frame=start_frame,
        output_end_frame=end_frame,
        source_start_frame=int(item.source_start_sec * (item.asset.source_fps or fps)) + 1,
        confidence=item.confidence,
        reason=item.reason,
        description=item.asset.description,
        has_audio=item.asset.has_audio,
        scale_percent=_scale_percent(item.asset, canvas_width, canvas_height, item.display_mode),
        display_mode=item.display_mode,
    )


def _scale_percent(asset: CatalogAsset, canvas_width: int, canvas_height: int, mode: DisplayMode) -> float:
    if not asset.width or not asset.height or canvas_width <= 0 or canvas_height <= 0:
        return 100.0
    width_ratio = canvas_width / asset.width
    height_ratio = canvas_height / asset.height
    if mode == "cover":
        ratio = max(width_ratio, height_ratio)
    elif mode == "contain":
        ratio = min(width_ratio, height_ratio)
    else:
        ratio = min(width_ratio, height_ratio) * 0.6
    return max(1.0, min(1000.0, ratio * 100.0))


def _write_plan(path: Path | None, mode: BrollMode, outcome: BrollPlanOutcome) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 3,
        "mode": mode,
        "provider": outcome.provider,
        "model": outcome.model,
        "error": outcome.error,
        "diagnostics": {
            "editorial_need_count": outcome.editorial_need_count,
            "catalog_asset_count": outcome.catalog_asset_count,
            "retrieved_asset_count": outcome.retrieved_asset_count,
            "filename_review_count": outcome.filename_review_count,
            "filename_described_count": outcome.filename_described_count,
            "filename_rejected_count": outcome.filename_rejected_count,
            "planner_rejection_count": outcome.planner_rejection_count,
            "safety_omission_count": outcome.safety_omission_count,
        },
        "placements": [{**asdict(item), "asset_path": str(item.asset_path)} for item in outcome.placements],
        "proposed": [
            {
                "id": item.id,
                "asset_id": item.asset.id,
                "asset_path": str(item.asset.path),
                "title": item.asset.title,
                "media_kind": item.asset.media_kind,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "source_start_sec": item.source_start_sec,
                "source_end_sec": item.source_end_sec,
                "confidence": item.confidence,
                "need_score": item.need_score,
                "relevance_score": item.relevance_score,
                "placement_safety_score": item.placement_safety_score,
                "source_grounding_score": item.source_grounding_score,
                "technical_quality_score": item.technical_quality_score,
                "display_mode": item.display_mode,
                "reason": item.reason,
            }
            for item in outcome.proposed
        ],
        "missing_assets": [asdict(item) for item in outcome.missing_assets],
        "web_candidates": [asdict(item) for item in outcome.web_candidates],
        "omitted": outcome.omitted,
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _asset_need_score(asset: CatalogAsset, need: BrollNeed) -> float:
    title = asset.title.casefold()
    description = asset.description.casefold()
    tags = " ".join(asset.tags).casefold()
    segments = " ".join(
        f"{segment.description} {' '.join(segment.tags)} {segment.visual_category} {segment.suitability}"
        for segment in asset.segments
    ).casefold()
    phrases = [need.description, *need.search_terms]
    score = 0.0
    for raw_phrase in phrases:
        phrase = raw_phrase.casefold().strip()
        if not phrase:
            continue
        if phrase in title:
            score += 20.0
        if phrase in description:
            score += 10.0
        if phrase in tags:
            score += 8.0
        if phrase in segments:
            score += 12.0
        for token in _search_tokens(phrase):
            if token in title:
                score += 5.0
            if token in description:
                score += 2.0
            if token in tags:
                score += 3.0
            if token in segments:
                score += 4.0
    if score > 0 and asset.description_source != "inferred":
        score += 0.5
    if score > 0 and asset.analysis_state == "ready":
        score += 0.75
    return score


def _grounding_score(
    value: dict[str, Any],
    asset: CatalogAsset,
    segment: CatalogSegment | None,
    confirmed_ranges: dict[str, list[tuple[float, float | None]]],
) -> float:
    model_score = _confidence(value.get("source_grounding_score"))
    if asset.id in confirmed_ranges:
        return 1.0
    if segment is not None:
        if segment.description_source == "user":
            return 1.0
        return min(model_score or segment.confidence, segment.confidence)
    if asset.media_kind == "image":
        return min(model_score or 0.85, 0.90 if asset.description_source != "inferred" else 0.45)
    ceiling = 0.75 if asset.description_source != "inferred" else 0.45
    return min(model_score, ceiling)


def _technical_quality_score(value: dict[str, Any], asset: CatalogAsset) -> float:
    model_score = _confidence(value.get("technical_quality_score"))
    if not asset.width or not asset.height:
        return min(model_score or 0.5, 0.5)
    pixels = asset.width * asset.height
    resolution_score = min(1.0, math.sqrt(pixels / (1280 * 720)))
    return min(model_score or resolution_score, max(0.35, resolution_score))


def _display_mode(value: dict[str, Any]) -> DisplayMode:
    mode = str(value.get("display_mode") or "cover").strip().lower()
    return mode if mode in {"cover", "contain", "overlay"} else "cover"  # type: ignore[return-value]


def _range_confirmed(
    source_start: float,
    source_end: float | None,
    ranges: Sequence[tuple[float, float | None]],
) -> bool:
    return any(
        abs(source_start - confirmed_start) <= 0.05
        and (
            source_end is None
            and confirmed_end is None
            or source_end is not None
            and confirmed_end is not None
            and abs(source_end - confirmed_end) <= 0.05
        )
        for confirmed_start, confirmed_end in ranges
    )


def _line_ranges(value: Any, subtitle_count: int) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        return []
    ranges: list[tuple[int, int]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start_line"))
            end = int(item.get("end_line"))
        except (TypeError, ValueError):
            continue
        if 1 <= start <= end <= subtitle_count:
            ranges.append((start, end))
    return ranges


def _overlaps_ranges(start: int, end: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= protected_end and end >= protected_start for protected_start, protected_end in ranges)


def _transcript_lines(subtitles: Sequence[Subtitle]) -> str:
    lines: list[str] = []
    used_chars = 0
    for index, subtitle in enumerate(subtitles, start=1):
        line = f"{index}\t{subtitle.start_time:.3f}\t{subtitle.end_time:.3f}\t{subtitle.text}"
        if used_chars + len(line) > MAX_TRANSCRIPT_CHARS:
            break
        lines.append(line)
        used_chars += len(line) + 1
    return "\n".join(lines)


def _search_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE) if len(token) >= 2]


def _string_list(value: Any, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()[:200]
        if text and text.casefold() not in {existing.casefold() for existing in result}:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _json_tags(value: Any) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return ()
    return _string_list(parsed, 50)


def _select_column(
    columns: set[str],
    name: str,
    fallback: str,
    *,
    table_alias: str = "a",
) -> str:
    prefix = f"{table_alias}." if table_alias else ""
    return f"{prefix}{name} AS {name}" if name in columns else f"{fallback} AS {name}"


def _json_object(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3]
    try:
        data = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("B-roll provider returned malformed JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("B-roll provider response must be a JSON object")
    return data


def _score(value: dict[str, Any], key: str, fallback: float) -> float:
    return _confidence(value[key]) if key in value else fallback


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number)) if math.isfinite(number) else 0.0


def _finite_non_negative(value: Any, message: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(message)
    return number


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
