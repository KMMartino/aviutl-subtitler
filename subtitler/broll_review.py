"""B-roll review presentation and validated descriptions."""

from __future__ import annotations

from typing import Any, Sequence

from .broll import FilenameReviewCandidate
from .models import Subtitle
from .review_exchange import ReviewError, ReviewStorage, exchange_review


def request_filename_descriptions(
    candidates: Sequence[FilenameReviewCandidate], subtitles: Sequence[Subtitle],
    frontend_protocol: str | None, storage: ReviewStorage | None = None,
) -> tuple[dict[str, str], set[str]]:
    if frontend_protocol != "stdio-v1":
        return {}, set()
    payload = [
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
            "descriptionRequired": item.description_required,
            "sceneLabel": next((scene.observed_label or scene.description for scene in item.asset.segments
                                if scene.start_sec <= item.source_start_sec < scene.end_sec), "")[:1000],
        }
        for item in candidates
    ]
    return exchange_review("broll", {"candidates": payload}, lambda value: parse_descriptions(value, candidates), storage=storage)


def parse_descriptions(response: dict[str, Any], candidates: Sequence[FilenameReviewCandidate]) -> tuple[dict[str, str], set[str]]:
    valid_ids = {item.id for item in candidates}
    descriptions: dict[str, str] = {}
    library_candidates: set[str] = set()
    seen: set[str] = set()
    by_id = {item.id: item for item in candidates}
    occupied: list[tuple[int, int]] = []
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        raise ReviewError("B-roll review is missing decisions")
    for item in decisions:
        if not isinstance(item, dict):
            raise ReviewError("Invalid B-roll review decision")
        candidate_id, decision = item.get("candidateId"), item.get("decision")
        if not isinstance(candidate_id, str) or candidate_id not in valid_ids or candidate_id in seen:
            raise ReviewError("B-roll review contains an unknown or duplicate candidate")
        seen.add(candidate_id)
        if decision == "describe":
            if not by_id[candidate_id].description_required:
                raise ReviewError("Choose or reject a scene candidate")
            description = item.get("description")
            if not isinstance(description, str) or not description.strip() or len(description) > 4000:
                raise ReviewError("B-roll descriptions must contain 1 to 4000 characters")
            descriptions[candidate_id] = description.strip()
        elif decision == "use_library":
            candidate = by_id[candidate_id]
            if not candidate.description_required:
                if any(candidate.start_line <= end and candidate.end_line >= start for start, end in occupied):
                    raise ReviewError("Choose only one B-roll alternative for each passage")
                occupied.append((candidate.start_line, candidate.end_line))
            library_candidates.add(candidate_id)
        elif decision != "reject":
            raise ReviewError("Invalid B-roll review decision")
    if seen != valid_ids:
        raise ReviewError("Every B-roll candidate requires a decision")
    return descriptions, library_candidates


def request_placement_choices(
    candidates: Sequence[FilenameReviewCandidate], subtitles: Sequence[Subtitle],
    frontend_protocol: str | None, storage: ReviewStorage | None = None,
) -> set[str]:
    _, selected = request_filename_descriptions(candidates, subtitles, frontend_protocol, storage)
    occupied: list[tuple[int, int]] = []
    for candidate in candidates:
        if candidate.id not in selected:
            continue
        if any(candidate.start_line <= end and candidate.end_line >= start for start, end in occupied):
            raise ReviewError("Choose only one B-roll alternative for each passage")
        occupied.append((candidate.start_line, candidate.end_line))
    return selected
