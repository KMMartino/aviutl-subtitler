"""Silence-review presentation and decision validation, separate from media editing."""

from __future__ import annotations

from typing import Any, Sequence

from .errors import SubtitlerError
from .review_exchange import ReviewStorage, exchange_review
from .silence_cut import SilenceCutCandidate, SilenceCutDecision, SilenceReviewResult


def request_review(
    candidates: Sequence[SilenceCutCandidate], frontend_protocol: str | None,
    storage: ReviewStorage | None = None,
) -> SilenceReviewResult:
    if frontend_protocol != "stdio-v1":
        raise SubtitlerError("Cut silence review mode requires the SubUtl desktop review interface")
    return exchange_review(
        "silence", {"candidates": [candidate.to_frontend() for candidate in candidates]},
        lambda value: parse_review_decisions(value, candidates), storage=storage,
    )


def parse_review_decisions(value: dict[str, Any], candidates: Sequence[SilenceCutCandidate]) -> SilenceReviewResult:
    raw_decisions = value.get("decisions")
    if not isinstance(raw_decisions, list):
        raise SubtitlerError("Cut silence review response is missing decisions")
    decisions: dict[str, SilenceCutDecision] = {}
    valid_ids = {candidate.id for candidate in candidates}
    valid_decisions = {"accept_cut", "reject_cut", "mark_and_reject"}
    for item in raw_decisions:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("candidateId"), str) or item["candidateId"] not in valid_ids
            or not isinstance(item.get("decision"), str) or item["decision"] not in valid_decisions
        ):
            raise SubtitlerError("Cut silence review response contains an invalid decision")
        candidate_id = str(item["candidateId"])
        if candidate_id in decisions:
            raise SubtitlerError("Cut silence review response contains a duplicate candidate")
        decisions[candidate_id] = item["decision"]
    if set(decisions) != valid_ids:
        raise SubtitlerError("Cut silence review requires a decision for every candidate")
    return SilenceReviewResult(value["reviewId"], decisions)

