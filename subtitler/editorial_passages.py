"""Pack complete semantic passages into bounded, speech-safe analysis work."""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from .errors import SubtitlerError


def _speech_spans(duration_ms: int, speech: list[dict[str, Any]]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for row in speech:
        start, end = row.get("start_ms"), row.get("end_ms")
        if type(start) is not int or type(end) is not int or start >= end:
            raise SubtitlerError("Speech evidence needs increasing integer source times")
        start, end = max(0, start), min(duration_ms, end)
        if start < end:
            spans.append((start, end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _containing(point: int, spans: list[tuple[int, int]], starts: list[int]) -> tuple[int, int] | None:
    index = bisect_right(starts, point) - 1
    if index >= 0 and spans[index][0] < point < spans[index][1]:
        return spans[index]
    return None


def semantic_boundary_candidates(duration_ms: int, facts: list[dict[str, Any]],
                                 speech: list[dict[str, Any]]) -> list[int]:
    """Return candidate passage ends; move edges inside speech to utterance edges.

    These are analysis boundaries, not approved edit points or speech handles.
    A zero-duration source has the single final boundary zero.
    """
    if type(duration_ms) is not int or duration_ms < 0:
        raise SubtitlerError("Source duration must be a nonnegative integer")
    spans = _speech_spans(duration_ms, speech)
    starts = [span[0] for span in spans]
    candidates = {duration_ms}
    for fact in facts:
        for key in ("start_ms", "end_ms"):
            point = fact.get(key)
            if type(point) is not int or not 0 < point <= duration_ms:
                continue
            containing = _containing(point, spans, starts)
            candidates.update(containing if containing else (point,))
    return sorted(point for point in candidates if point > 0 or duration_ms == 0)


def build_work_packets(passages: list[dict[str, Any]], duration_ms: int,
                       speech: list[dict[str, Any]], target_ms: int = 300000,
                       maximum_ms: int = 480000) -> list[dict[str, Any]]:
    """Pack whole passages near target; split only individual passages over maximum.

    An uninterrupted utterance may exceed the soft maximum. Never split speech
    merely to honor the compute target. Every packet covers original source time.
    """
    if (type(duration_ms) is not int or duration_ms < 0 or type(target_ms) is not int
            or type(maximum_ms) is not int or not 0 < target_ms <= maximum_ms):
        raise SubtitlerError("Work packet durations need 0 < target <= maximum and a valid source duration")
    spans = _speech_spans(duration_ms, speech)
    starts = [span[0] for span in spans]
    cursor = 0
    seen: set[str] = set()
    validated = []
    for index, passage in enumerate(passages):
        start, end = passage.get("start_ms"), passage.get("end_ms")
        identifier = passage.get("passage_id", passage.get("id", f"passage-{index + 1:04d}"))
        if (type(start) is not int or type(end) is not int or start != cursor
                or not start < end <= duration_ms or not isinstance(identifier, str)
                or not identifier.strip() or identifier in seen):
            raise SubtitlerError("Semantic passages must have unique IDs and contiguous ordered source coverage")
        if _containing(start, spans, starts) or _containing(end, spans, starts):
            raise SubtitlerError("A semantic passage boundary falls inside spoken evidence")
        validated.append((start, end, identifier))
        seen.add(identifier)
        cursor = end
    if cursor != duration_ms:
        raise SubtitlerError("Semantic passages must cover the complete source")
    fragments: list[dict[str, Any]] = []
    for start, end, identifier in validated:
        parts = []
        cursor = start
        while end - cursor > maximum_ms:
            point = cursor + target_ms
            containing = _containing(point, spans, starts)
            if containing:
                choices = [edge for edge in containing if cursor < edge <= min(end, cursor + maximum_ms)]
                point = min(choices, key=lambda edge: (abs(edge - point), edge)) if choices else containing[1]
            parts.append((cursor, point))
            cursor = point
        if cursor < end:
            parts.append((cursor, end))
        fragments.extend({"start_ms": left, "end_ms": right, "passage_id": identifier,
                          "split_parent": len(parts) > 1} for left, right in parts)
    packets = []
    index = 0
    while index < len(fragments):
        start = fragments[index]["start_ms"]
        eligible = []
        next_index = index
        while next_index < len(fragments) and fragments[next_index]["end_ms"] - start <= maximum_ms:
            eligible.append(next_index)
            next_index += 1
        if eligible and eligible[-1] == len(fragments) - 1:
            last = eligible[-1]
        elif eligible:
            last = min(eligible, key=lambda i: (abs(fragments[i]["end_ms"] - start - target_ms), -i))
        else:
            last = index  # A single uninterrupted utterance exceeds the soft cap.
        included = fragments[index:last + 1]
        packets.append({"start_ms": start, "end_ms": included[-1]["end_ms"],
                        "passage_ids": list(dict.fromkeys(f["passage_id"] for f in included)),
                        "split_parent": any(f["split_parent"] for f in included)})
        index = last + 1
    return packets
