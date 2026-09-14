"""One source-timed gap operation, parameterized for cuts and editing guides."""

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class SpeechGapPolicy:
    leading_handle: float
    trailing_handle: float
    minimum_gap: float
    minimum_cut: float
    include_edges: bool = False


@dataclass(frozen=True)
class SpeechGap:
    silence_start: float
    silence_end: float
    cut_start: float
    cut_end: float


def find_speech_gaps(
    speech: Iterable[tuple[float, float]], policy: SpeechGapPolicy,
    *, duration: float | None = None,
) -> list[SpeechGap]:
    """Return gaps in seconds; merge overlapping evidence before adding handles."""
    numbers = (policy.leading_handle, policy.trailing_handle, policy.minimum_gap, policy.minimum_cut)
    if any(not math.isfinite(n) or n < 0 for n in numbers):
        raise ValueError("Speech-gap policy values must be finite and nonnegative")
    if duration is not None and (not math.isfinite(duration) or duration < 0):
        raise ValueError("Invalid source duration")
    if policy.include_edges and duration is None:
        raise ValueError("Edge gaps require a source duration")
    merged: list[tuple[float, float]] = []
    for start, end in sorted(speech):
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Speech times must be finite")
        start, end = max(0, start), min(duration, end) if duration is not None else end
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    gaps = [(left[1], right[0], policy.trailing_handle, policy.leading_handle)
            for left, right in zip(merged, merged[1:])]
    if policy.include_edges and duration is not None:
        gaps = ([(0, merged[0][0], 0, policy.leading_handle), *gaps,
                 (merged[-1][1], duration, policy.trailing_handle, 0)]
                if merged else [(0, duration, 0, 0)])
    return [SpeechGap(start, end, start + trailing, end - leading)
            for start, end, trailing, leading in gaps
            if end > start and end - start >= policy.minimum_gap
            and end - leading - start - trailing > 0
            and end - leading - start - trailing + 1e-9 >= policy.minimum_cut]
