"""Review transport with durable requests and validated, revision-bound responses."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from .artifact_io import write_json_artifact
from .errors import SubtitlerError

FRONTEND_EVENT_PREFIX = "@@SUBUTL_EVENT@@"
T = TypeVar("T")


class ReviewError(SubtitlerError):
    """A review is unfinished or invalid; optional stages must not swallow it."""


def emit_frontend_event(event_type: str, **payload: Any) -> None:
    print(FRONTEND_EVENT_PREFIX + json.dumps(
        {"type": event_type, **payload}, ensure_ascii=False, separators=(",", ":"),
    ), flush=True)


@dataclass(frozen=True)
class ReviewStorage:
    path: Path
    input_revision: str


def exchange_review(
    kind: str,
    payload: dict[str, Any],
    validate: Callable[[dict[str, Any]], T],
    *,
    storage: ReviewStorage | None = None,
) -> T:
    """Save before waiting; never persist an invalid response or reuse stale input."""
    request = {"type": f"{kind}-review-required", "reviewId": str(uuid.uuid4()), **payload}
    record: dict[str, Any] = {
        "type": "review_exchange", "schema_version": 1,
        "input_revision": storage.input_revision if storage else None,
        "request": request, "response": None,
    }
    if storage and storage.path.exists():
        try:
            previous = json.loads(storage.path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict) or previous.get("type") != "review_exchange" or previous.get("schema_version") != 1:
                raise ValueError("unsupported review contract")
            old_request = previous["request"]
            if not isinstance(old_request, dict) or not isinstance(old_request.get("reviewId"), str):
                raise ValueError("invalid review request")
            if previous.get("input_revision") == storage.input_revision and {
                key: value for key, value in old_request.items() if key != "reviewId"
            } == {key: value for key, value in request.items() if key != "reviewId"}:
                record, request = previous, old_request
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise ReviewError(f"Could not load review {storage.path}: {exc}") from exc
    if storage:
        write_json_artifact(storage.path, record)
    response = record.get("response")
    if response is None:
        emit_frontend_event(request["type"], **{k: v for k, v in request.items() if k != "type"})
        line = sys.stdin.readline()
        if not line:
            suffix = "; restart the same run to resume" if storage else ""
            raise ReviewError("Review ended before decisions were submitted" + suffix)
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError("Review returned invalid JSON") from exc
    if not isinstance(response, dict) or response.get("type") != f"{kind}-review-result" or response.get("reviewId") != request["reviewId"]:
        raise ReviewError("Review response did not match the active review")
    result = validate(response)
    if storage and record.get("response") is None:
        record["response"] = response
        write_json_artifact(storage.path, record)
    return result
