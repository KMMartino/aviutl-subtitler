"""Resolution probing and conservative wide-recording layout detection."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .errors import SubtitlerError


DESIGN_WIDTH = 2560
DESIGN_HEIGHT = 1440
GAMEPLAY_ASPECT = 16 / 9


@dataclass(frozen=True)
class VideoGeometry:
    width: int
    height: int
    frame_rate: float
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class WideRecordingLayout:
    source_width: int
    source_height: int
    gameplay_width: int
    facecam_left: int
    facecam_bottom: int

    @property
    def output_width(self) -> int:
        return self.gameplay_width

    @property
    def output_height(self) -> int:
        return self.source_height

    def to_dict(self) -> dict[str, int]:
        return {
            "source_width": self.source_width,
            "source_height": self.source_height,
            "gameplay_width": self.gameplay_width,
            "facecam_left": self.facecam_left,
            "facecam_bottom": self.facecam_bottom,
        }


@dataclass(frozen=True)
class VisualPlacement:
    crop: tuple[int, int, int, int] | None
    scale_percent: float
    x: float
    y: float


def wide_recording_placements(layout: WideRecordingLayout) -> tuple[VisualPlacement, VisualPlacement]:
    primary = VisualPlacement(
        crop=(0, 0, 0, layout.source_width - layout.gameplay_width),
        scale_percent=100.0,
        x=0.0,
        y=0.0,
    )
    facecam_width = layout.source_width - layout.facecam_left
    facecam_height = layout.facecam_bottom
    overlay = top_right_overlay_placement(
        layout.output_width,
        layout.output_height,
        facecam_width,
        facecam_height,
        crop=(0, layout.source_height - layout.facecam_bottom, layout.facecam_left, 0),
    )
    return primary, overlay


def cover_placement(
    canvas_width: int, canvas_height: int, source_width: int, source_height: int
) -> VisualPlacement:
    if min(canvas_width, canvas_height, source_width, source_height) <= 0:
        return VisualPlacement(None, 100.0, 0.0, 0.0)
    scale = max(canvas_width / source_width, canvas_height / source_height)
    return VisualPlacement(None, scale * 100.0, 0.0, 0.0)


def top_right_overlay_placement(
    canvas_width: int,
    canvas_height: int,
    source_width: int,
    source_height: int,
    *,
    crop: tuple[int, int, int, int] | None = None,
) -> VisualPlacement:
    if min(canvas_width, canvas_height, source_width, source_height) <= 0:
        return VisualPlacement(crop, 100.0, 0.0, 0.0)
    # A facecam occupies at most roughly the upper-right third of the canvas.
    scale = min(1.0, canvas_width * 0.32 / source_width, canvas_height * 0.34 / source_height)
    displayed_width = source_width * scale
    displayed_height = source_height * scale
    return VisualPlacement(
        crop,
        scale * 100.0,
        (canvas_width - displayed_width) / 2.0,
        -(canvas_height - displayed_height) / 2.0,
    )


def probe_video_geometry(path: Path) -> VideoGeometry:
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate:format=duration",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        rate = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
        duration = float(payload.get("format", {}).get("duration") or 0.0)
        if width <= 0 or height <= 0:
            raise ValueError("non-positive video dimensions")
        return VideoGeometry(width, height, rate, max(0.0, duration))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise SubtitlerError(f"Could not determine video dimensions for {path}: {exc}") from exc


def analyze_wide_recording(path: Path, geometry: VideoGeometry | None = None) -> WideRecordingLayout | None:
    geometry = geometry or probe_video_geometry(path)
    gameplay_width = round(geometry.height * GAMEPLAY_ASPECT)
    # Ordinary 16:9 and mildly non-standard sources must never be split.
    if gameplay_width <= 0 or geometry.width < gameplay_width * 1.12:
        return None
    sample_width = min(960, geometry.width)
    sample_height = max(1, round(geometry.height * sample_width / geometry.width))
    sample_gameplay_width = round(gameplay_width * sample_width / geometry.width)
    if sample_width - sample_gameplay_width < 32:
        return None

    frames: list[bytes] = []
    duration = geometry.duration_seconds
    times = (0.15, 0.38, 0.62, 0.85) if duration > 1 else (0.0,)
    for fraction in times:
        frame = _read_gray_frame(
            path,
            duration * fraction if duration > 0 else 0.0,
            sample_width,
            sample_height,
        )
        if frame is not None:
            frames.append(frame)
    if not frames:
        return None

    boundary = _detect_facecam_bottom(
        frames,
        sample_width,
        sample_height,
        sample_gameplay_width,
    )
    if boundary is None:
        return None
    facecam_bottom = round(boundary * geometry.height / sample_height)
    facecam_bottom = max(1, min(geometry.height, facecam_bottom))
    return WideRecordingLayout(
        source_width=geometry.width,
        source_height=geometry.height,
        gameplay_width=gameplay_width,
        facecam_left=gameplay_width,
        facecam_bottom=facecam_bottom,
    )


def _read_gray_frame(path: Path, seconds: float, width: int, height: int) -> bytes | None:
    try:
        completed = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", f"scale={width}:{height}:flags=area,format=gray",
                "-f", "rawvideo", "pipe:1",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    expected = width * height
    return completed.stdout if len(completed.stdout) == expected else None


def _detect_facecam_bottom(
    frames: list[bytes], width: int, height: int, right_start: int
) -> int | None:
    """Find a bright/non-black upper-right region over a black lower-right region."""
    right_width = width - right_start
    if right_width <= 0 or height < 8:
        return None
    row_activity: list[float] = []
    for y in range(height):
        per_frame = []
        start = y * width + right_start
        end = start + right_width
        for frame in frames:
            row = frame[start:end]
            per_frame.append(sum(value >= 20 for value in row) / right_width)
        row_activity.append(float(median(per_frame)))

    minimum = max(2, round(height * 0.2))
    maximum = min(height - 2, round(height * 0.9))
    best: tuple[float, int, float, float] | None = None
    for boundary in range(minimum, maximum + 1):
        top = sum(row_activity[:boundary]) / boundary
        bottom = sum(row_activity[boundary:]) / (height - boundary)
        score = top - bottom
        if best is None or score > best[0]:
            best = (score, boundary, top, bottom)
    if best is None:
        return None
    score, boundary, top, bottom = best
    # Conservative thresholds prevent ultrawide gameplay or letterboxing from
    # being mistaken for the common gameplay + facecam capture arrangement.
    if score < 0.18 or top < 0.22 or bottom > 0.08:
        return None
    return boundary


def _parse_rate(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    numerator, separator, denominator = value.partition("/")
    try:
        result = float(numerator) / float(denominator) if separator else float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return result if math.isfinite(result) and result > 0 else 0.0
