"""Source layout and EXO export operations, independent of workflow modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SubtitlerError
from .exo import generate_exo_file, write_exo
from .media_layout import (
    DESIGN_HEIGHT, WideRecordingLayout, wide_recording_placements,
)
from .models import BrollPlacement, ExoCompositeMediaClip, ExoMarker, ExoMediaPlan, ExoMediaSegment, ExoSettings, Subtitle
from .source_inspection import inspect_visual_layout
from .silence_cut import build_rendered_media_plan, probe_media_streams


@dataclass(frozen=True)
class SourceLayout:
    settings: ExoSettings
    wide_recording: WideRecordingLayout | None = None


@dataclass(frozen=True)
class ExoExportRequest:
    source_path: Path
    output_path: Path
    duration_sec: float
    layout: SourceLayout
    subtitles: list[Subtitle]
    chapter_markers: list[ExoMarker] = field(default_factory=list)
    qa_markers: list[ExoMarker] = field(default_factory=list)
    media_plan: ExoMediaPlan | None = None
    broll_placements: list[BrollPlacement] = field(default_factory=list)


def prepare_source_layout(source_path: Path, exo_config: dict[str, Any]) -> SourceLayout:
    geometry, wide = inspect_visual_layout(source_path)
    width = wide.output_width if wide else geometry.width if geometry else int(exo_config["width"])
    height = wide.output_height if wide else geometry.height if geometry else int(exo_config["height"])
    scale = height / DESIGN_HEIGHT
    return SourceLayout(
        ExoSettings(
            width=width, height=height, rate=int(exo_config["fps"]), font=exo_config["font"],
            font_size=max(1, round(int(exo_config["font_size"]) * scale)),
            y_position=float(exo_config["y_position"]) * scale,
        ),
        wide,
    )


def export_exo(request: ExoExportRequest) -> Path:
    """Export a prepared timeline without owning upstream rendered-media cleanup."""
    settings = request.layout.settings
    media_plan = request.media_plan
    if request.broll_placements and media_plan is None:
        try:
            if probe_media_streams(request.source_path).has_video:
                media_plan = build_rendered_media_plan(request.source_path, request.duration_sec, settings.rate)
        except SubtitlerError as exc:
            print(
                "Warning: Could not include the primary video in the B-roll EXO; "
                f"continuing with B-roll assets and subtitles only. {exc}", flush=True,
            )
    composite = []
    if request.layout.wide_recording is not None:
        source_path = media_plan.source_path if media_plan else request.source_path
        segments = media_plan.segments if media_plan else [
            ExoMediaSegment(1, max(1, round(request.duration_sec * settings.rate)), 1, 1)
        ]
        primary, overlay = wide_recording_placements(request.layout.wide_recording)
        composite = [
            ExoCompositeMediaClip(
                video_path=source_path, audio_path=source_path, segment=segment,
                overlay_video_path=source_path, overlay_audio_path=source_path,
                video_crop=primary.crop, video_scale_percent=primary.scale_percent,
                video_x=primary.x, video_y=primary.y,
                overlay_crop=overlay.crop, overlay_scale_percent=overlay.scale_percent,
                overlay_x=overlay.x, overlay_y=overlay.y,
                # Preserve paired layers without playing the same audio twice.
                overlay_audio_volume=0.0,
            )
            for segment in segments
        ]
        media_plan = None
    content = generate_exo_file(
        request.subtitles, settings, request.duration_sec, insert_initial_empty=True,
        chapter_markers=request.chapter_markers, mistranscription_markers=request.qa_markers,
        media_plan=media_plan, composite_media_clips=composite, broll_placements=request.broll_placements,
    )
    write_exo(request.output_path, content)
    return request.output_path
