"""Spatial change observations between sampled images, without event inference."""

from pathlib import Path
import subprocess
from typing import Any, Sequence

import numpy as np
from numpy.typing import NDArray

from .errors import SubtitlerError


def sampled_regional_motion(
    paths: Sequence[Path], timestamps_ms: Sequence[int], *, minimum_span_ms: int = 24000,
    ffmpeg: str = "ffmpeg",
) -> list[dict[str, Any]]:
    """Find sustained unchanged regions on an 8x4 grid of normalized images.

    Bounds identify compared samples, not the onset or continuity of stillness.
    Images are only read. Low-resolution grayscale and a small difference floor
    suppress compression noise; no fixed camera or gameplay region is assumed.
    """
    if len(paths) != len(timestamps_ms):
        raise ValueError("Every sampled frame needs a timestamp")
    if len(paths) > 64:
        raise ValueError("Regional motion accepts at most 64 sampled frames")
    if minimum_span_ms <= 0 or any(type(t) is not int or t < 0 for t in timestamps_ms):
        raise ValueError("Motion timestamps and minimum span must be positive")
    if any(right <= left for left, right in zip(timestamps_ms, timestamps_ms[1:])):
        raise ValueError("Motion samples must be in strictly increasing source time")
    observations: list[dict[str, Any]] = []
    previous = None
    stable = None
    moving: NDArray[np.bool_] = np.zeros(32, dtype=bool)
    start = end = comparisons = 0

    def finish() -> None:
        if stable is None or comparisons < 2 or end - start < minimum_span_ms:
            return
        observations.append({
            "start_ms": start, "end_ms": end, "sample_comparisons": comparisons,
            "grid_columns": 8, "grid_rows": 4,
            "unchanged_cells": np.flatnonzero(stable).tolist(),
            "moving_cells": np.flatnonzero(moving).tolist(),
            "observation": ("dominant_region_unchanged_with_local_motion" if moving.any()
                            else "sampled_frame_unchanged" if stable.all()
                            else "dominant_region_unchanged"),
        })

    for index, path in enumerate(paths):
        try:
            decoded = subprocess.run(
                [ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=320:180:flags=area", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                capture_output=True, timeout=15, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SubtitlerError("Could not decode regional-motion sample") from exc
        if decoded.returncode or len(decoded.stdout) != 320 * 180:
            raise SubtitlerError("Regional-motion sample did not decode to the expected image size")
        current = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(180, 320).astype(np.float32)
        if previous is not None:
            difference = np.abs(current - previous).reshape(4, 45, 8, 40).mean(axis=(1, 3)).ravel()
            pair_stable = difference <= 1.5
            pair_moving = difference >= 4.0
            common = pair_stable if stable is None else stable & pair_stable
            if np.count_nonzero(common) < 24:
                finish()
                stable = None
                comparisons = 0
                moving = np.zeros(32, dtype=bool)
                common = pair_stable
            if np.count_nonzero(common) >= 24:
                if stable is None:
                    start = timestamps_ms[index - 1]
                stable = common
                end = timestamps_ms[index]
                comparisons += 1
                moving |= pair_moving
        previous = current
    finish()
    return observations
