"""Source inspection shared by export layouts and editorial compositions."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio import get_media_duration
from .errors import SubtitlerError
from .media_layout import VideoGeometry, WideRecordingLayout, analyze_wide_recording, probe_video_geometry


@dataclass(frozen=True)
class SourceInspectionRequest:
    audio_path: Path
    visual_path: Path
    paired: bool = False
    expected_visual_duration_ms: int | None = None


@dataclass(frozen=True)
class SourceInspection:
    audio_duration_ms: int
    visual_duration_ms: int
    frame_rate: float
    visual_geometry: VideoGeometry | None
    audio_geometry: VideoGeometry | None
    wide_layout: WideRecordingLayout | None


def inspect_visual_layout(path: Path, *, detect_wide: bool = True) -> tuple[VideoGeometry | None, WideRecordingLayout | None]:
    geometry, wide = None, None
    try:
        geometry = probe_video_geometry(path)
        if detect_wide:
            wide = analyze_wide_recording(path, geometry)
    except SubtitlerError:
        pass
    return geometry, wide


def inspect_recording(request: SourceInspectionRequest) -> SourceInspection:
    audio_duration = round(get_media_duration(request.audio_path) * 1000)
    visual_duration = round(get_media_duration(request.visual_path) * 1000)
    if audio_duration <= 0 or visual_duration <= 0:
        raise SubtitlerError(f"Could not determine paired media duration for {request.visual_path.name}")
    expected = request.expected_visual_duration_ms
    if expected is not None and abs(expected - visual_duration) > max(2000, expected * 0.01):
        raise SubtitlerError(f"Source duration changed after project creation: {request.visual_path.name}")
    frame_rate = _probe_frame_rate(request.visual_path)
    visual, wide = inspect_visual_layout(request.visual_path, detect_wide=not request.paired)
    audio, _ = inspect_visual_layout(request.audio_path, detect_wide=False) if request.paired else (None, None)
    if request.paired:
        if frame_rate <= 0:
            raise SubtitlerError(f"Could not determine gameplay frame rate for {request.visual_path.name}")
        if abs(audio_duration - visual_duration) > (10.0 / frame_rate) * 1000.0 + 1.0:
            raise SubtitlerError("Facecam and gameplay lengths differ by more than 10 gameplay frames: "
                                 f"{request.audio_path.name} / {request.visual_path.name}")
    return SourceInspection(audio_duration, visual_duration, frame_rate, visual, audio, wide)


def _probe_frame_rate(path: Path) -> float:
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return 0.0
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        return 0.0
    for field in ("avg_frame_rate", "r_frame_rate"):
        value = _parse_frame_rate(streams[0].get(field))
        if value > 0:
            return value
    return 0.0


def _parse_frame_rate(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    numerator, separator, denominator = value.partition("/")
    try:
        result = float(numerator) / float(denominator) if separator else float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return result if result > 0 else 0.0


