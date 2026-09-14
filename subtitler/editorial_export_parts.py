"""Whole-recording export groups and their source-local review timelines."""

from __future__ import annotations

import re
from typing import Any

from .errors import SubtitlerError

MAX_EXO_DURATION_MS = 12 * 60 * 60 * 1000


def export_source_groups(project: dict[str, Any]) -> list[list[dict[str, Any]]]:
    sources = sorted(project["sources"], key=lambda source: source["order"])
    if not sources:
        return []
    # First find the minimum number of contiguous groups under the limit.
    # A recording longer than the limit stays intact in a group of its own.
    groups: list[list[dict[str, Any]]] = []
    duration = 0
    for source in sources:
        length = int(source["duration_ms"])
        if not groups or duration + length > MAX_EXO_DURATION_MS:
            groups.append([])
            duration = 0
        groups[-1].append(source)
        duration += length
    # Move a boundary toward equal duration only when both adjacent groups
    # improve and the receiving group remains within the limit.
    changed = True
    while changed:
        changed = False
        for left, right in zip(groups, groups[1:]):
            a = sum(int(s["duration_ms"]) for s in left)
            b = sum(int(s["duration_ms"]) for s in right)
            if len(left) > 1:
                length = int(left[-1]["duration_ms"])
                if b + length <= MAX_EXO_DURATION_MS and abs(a - b - 2 * length) < abs(a - b):
                    right.insert(0, left.pop())
                    changed = True
                    continue
            if len(right) > 1:
                length = int(right[0]["duration_ms"])
                if a + length <= MAX_EXO_DURATION_MS and abs(a - b + 2 * length) < abs(a - b):
                    left.append(right.pop(0))
                    changed = True
    return groups


def project_for_review(project: dict[str, Any], exo_text: str) -> dict[str, Any]:
    """Resolve a saved part by linked footage, even after the EXO was renamed."""
    parts = project.get("outputs", {}).get("exo_parts", [])
    if len(parts) < 2:
        return project
    media = {name.strip().casefold() for name in re.findall(r"(?m)^file=(.+)$", exo_text)}
    sources = project["sources"]
    present = {s["source_id"] for s in sources if str(s["visual_path"]).casefold() in media}
    matches = [part for part in parts if set(part["source_ids"]) == present]
    if len(matches) != 1:
        raise SubtitlerError("This EXO must contain the recordings from exactly one exported part.")
    return {**project, "sources": [s for s in sources if s["source_id"] in present]}
