"""Build the editable AviUtl lookup project for an editorial analysis."""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path
from typing import Any

from .editorial_presentation import (
    EDITORIAL_LAYER_ORDER,
    PresentedEditorialItem,
    category_label,
    presented_editorial_items,
    primary_suggestion,
)
from .errors import SubtitlerError
from .exo import generate_exo_file, time_to_frame, write_exo
from .media_layout import (
    DESIGN_HEIGHT,
    DESIGN_WIDTH,
    WideRecordingLayout,
    analyze_wide_recording,
    cover_placement,
    probe_video_geometry,
    top_right_overlay_placement,
    wide_recording_placements,
)
from .models import ExoCompositeMediaClip, ExoMarker, ExoMediaSegment, ExoSettings, Subtitle


def write_editorial_exo(path: Path, artifact: dict[str, Any]) -> None:
    """Write linked source media, transcript subtitles, and editorial markers."""
    sources = sorted(artifact["sources"], key=lambda item: item["order"])
    settings = _canvas_settings(sources)
    if artifact.get("subtitle_mode", "full") == "emphasis":
        settings.y_position = 708.0 * settings.layout_scale
    clips: list[ExoCompositeMediaClip] = []
    subtitles: list[Subtitle] = []
    source_offsets: dict[str, float] = {}
    offset_seconds = 0.0
    next_group_id = 1
    action_group_ids: dict[str, int] = {}
    final_actions = [
        item
        for item in artifact.get("editorial_map", {}).get("final_actions", [])
        if isinstance(item, dict)
    ]

    for source in sources:
        visual_width, visual_height, audio_width, audio_height, wide_layout = (
            _source_layout(source)
        )
        primary = cover_placement(
            settings.width, settings.height, visual_width, visual_height
        )
        overlay = None
        overlay_audio_volume = 100.0
        if wide_layout is not None:
            primary, overlay = wide_recording_placements(wide_layout)
            overlay_audio_volume = 0.0
        elif source.get("media_mode") == "paired":
            overlay = top_right_overlay_placement(
                settings.width,
                settings.height,
                audio_width,
                audio_height,
            )
        duration_seconds = int(source["duration_ms"]) / 1000.0
        source_offsets[source["source_id"]] = offset_seconds
        boundaries = _source_edit_boundaries(source, final_actions)
        for start_ms, end_ms in zip(boundaries, boundaries[1:]):
            output_start = time_to_frame(offset_seconds + start_ms / 1000.0, settings.rate)
            output_end = max(
                output_start,
                time_to_frame(offset_seconds + end_ms / 1000.0, settings.rate) - 1,
            )
            segment = ExoMediaSegment(
                output_start,
                output_end,
                time_to_frame(start_ms / 1000.0, settings.rate),
                next_group_id,
            )
            clips.append(
                ExoCompositeMediaClip(
                    video_path=Path(source["visual_path"]),
                    audio_path=Path(source["visual_path"]),
                    segment=segment,
                    overlay_video_path=(
                        Path(source["audio_path"])
                        if source.get("media_mode") == "paired" or wide_layout is not None
                        else None
                    ),
                    overlay_audio_path=(
                        Path(source["audio_path"])
                        if source.get("media_mode") == "paired" or wide_layout is not None
                        else None
                    ),
                    video_crop=primary.crop,
                    video_scale_percent=primary.scale_percent,
                    video_x=primary.x,
                    video_y=primary.y,
                    overlay_crop=overlay.crop if overlay is not None else None,
                    overlay_scale_percent=(
                        overlay.scale_percent if overlay is not None else 100.0
                    ),
                    overlay_x=overlay.x if overlay is not None else 0.0,
                    overlay_y=overlay.y if overlay is not None else 0.0,
                    overlay_audio_volume=overlay_audio_volume,
                )
            )
            for action in final_actions:
                if (
                    str(action.get("source_id") or "") == str(source["source_id"])
                    and _integer(action.get("start_ms")) == start_ms
                    and _integer(action.get("end_ms")) == end_ms
                    and action.get("action_id")
                ):
                    action_group_ids[str(action["action_id"])] = next_group_id
            next_group_id += 1
        if artifact.get("subtitle_mode", "full") == "full":
            subtitles.extend(_source_subtitles(source, offset_seconds))
        offset_seconds += duration_seconds

    if artifact.get("subtitle_mode", "full") == "emphasis":
        subtitles.extend(_emphasized_subtitles(artifact, source_offsets))

    presented = presented_editorial_items(artifact)
    marker_layers = _editorial_marker_layers(artifact, source_offsets, presented)
    number_markers = _editorial_number_markers(
        presented, source_offsets, action_group_ids
    )
    content = generate_exo_file(
        subtitles,
        settings,
        offset_seconds,
        insert_initial_empty=False,
        composite_media_clips=clips,
        subtitle_background=False,
        additional_marker_layers=marker_layers,
        segment_number_markers=number_markers,
        additional_marker_font_scale=1.2,
    )
    write_exo(path, content)


def _source_subtitles(source: dict[str, Any], offset_seconds: float) -> list[Subtitle]:
    transcription = source.get("stages", {}).get("transcription", {}).get("output")
    if not isinstance(transcription, dict):
        return []
    timing_value = transcription.get("timing_path")
    text_value = transcription.get("text_path")
    if not isinstance(timing_value, str) or not isinstance(text_value, str):
        return []
    timing_path = Path(timing_value)
    text_path = Path(text_value)
    try:
        numbered_text = text_path.read_text(encoding="utf-8").splitlines()
        texts = []
        for line in numbered_text:
            _, separator, text = line.partition(". ")
            texts.append(text if separator else line)
        with timing_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SubtitlerError(
            f"Could not load transcript artifacts for editorial EXO: {source['original_name']}: {exc}"
        ) from exc
    if len(rows) != len(texts):
        raise SubtitlerError(
            f"Transcript timing/text count mismatch for editorial EXO: {source['original_name']}"
        )
    result = []
    for row, text in zip(rows, texts):
        try:
            start = float(row["start"])
            end = float(row["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SubtitlerError(
                f"Transcript timing artifact contains an invalid row: {source['original_name']}"
            ) from exc
        if text.strip() and end > start:
            result.append(Subtitle(offset_seconds + start, offset_seconds + end, text.strip()))
    return result


def _emphasized_subtitles(
    artifact: dict[str, Any], source_offsets: dict[str, float]
) -> list[Subtitle]:
    result: list[Subtitle] = []
    values = artifact.get("editorial_map", {}).get("emphasized_phrases", [])
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict) or item.get("timing_verified") is not True:
            continue
        source_id = str(item.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        try:
            start = source_offsets[source_id] + float(item["start_ms"]) / 1000.0
            end = source_offsets[source_id] + float(item["end_ms"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if text and end > start:
            result.append(
                Subtitle(
                    start,
                    end,
                    text,
                    outline_color=_emphasis_outline_color(
                        item.get("emphasis_energy")
                    ),
                )
            )
    return sorted(result, key=lambda item: (item.start_time, item.end_time))


def _editorial_marker_layers(
    artifact: dict[str, Any],
    source_offsets: dict[str, float],
    presented_items: list[PresentedEditorialItem] | None = None,
) -> list[list[ExoMarker]]:
    markers: list[tuple[int, ExoMarker]] = []
    for presented in presented_items or presented_editorial_items(artifact):
        # Primary directions now use only the grouped segment number and the
        # HTML report. Keep concrete local accents such as punch-ins in EXO.
        if presented.kind != "creative":
            continue
        item = presented.item
        source_id = str(item.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        try:
            start = max(0.0, float(item.get("start_ms", 0)) / 1000.0)
            end = max(start + 1 / 60, float(item.get("end_ms", 0)) / 1000.0)
        except (TypeError, ValueError):
            continue
        offset = source_offsets[source_id]
        markers.append(
            (
                _category_order(presented.category),
                ExoMarker(
                offset + start,
                offset + end,
                _human_marker_text(presented, str(artifact.get("output_locale", "en"))),
                ),
            )
        )
    lanes: list[list[ExoMarker]] = []
    lane_ends: list[float] = []
    for _, marker in sorted(
        markers,
        key=lambda value: (
            value[1].start_time,
            value[1].end_time,
            value[0],
            value[1].text,
        ),
    ):
        for index, occupied_until in enumerate(lane_ends):
            if marker.start_time >= occupied_until:
                lanes[index].append(marker)
                lane_ends[index] = marker.end_time
                break
        else:
            lanes.append([marker])
            lane_ends.append(marker.end_time)
    return lanes


def _category_order(category: str) -> int:
    try:
        return EDITORIAL_LAYER_ORDER.index(category)
    except ValueError:
        return len(EDITORIAL_LAYER_ORDER)


def _editorial_number_markers(
    presented_items: list[PresentedEditorialItem],
    source_offsets: dict[str, float],
    action_group_ids: dict[str, int],
) -> list[ExoMarker]:
    result = []
    for presented in presented_items:
        if presented.kind != "recommendation":
            continue
        number = _integer(presented.item.get("direction_number"))
        source_id = str(presented.item.get("source_id") or "")
        if number <= 0 or source_id not in source_offsets:
            continue
        start = max(0.0, _integer(presented.item.get("start_ms")) / 1000.0)
        end = max(start + 1 / 60, _integer(presented.item.get("end_ms")) / 1000.0)
        action_id = str(presented.item.get("action_id") or "")
        result.append(
            ExoMarker(
                source_offsets[source_id] + start,
                source_offsets[source_id] + end,
                str(number),
                group_id=action_group_ids.get(action_id),
            )
        )
    return result


def _source_edit_boundaries(
    source: dict[str, Any], final_actions: list[dict[str, Any]]
) -> list[int]:
    duration_ms = int(source["duration_ms"])
    values = {0, duration_ms}
    source_id = str(source["source_id"])
    for action in final_actions:
        if str(action.get("source_id") or "") != source_id:
            continue
        start_ms = max(0, min(duration_ms, _integer(action.get("start_ms"))))
        end_ms = max(0, min(duration_ms, _integer(action.get("end_ms"))))
        if end_ms > start_ms:
            values.update((start_ms, end_ms))
    return sorted(values)


def _human_marker_text(presented: PresentedEditorialItem, locale: str = "en") -> str:
    summary = primary_suggestion(presented, locale)
    wrapped = textwrap.wrap(
        summary,
        width=30,
        break_long_words=True,
        break_on_hyphens=False,
        max_lines=2,
        placeholder="...",
    )
    return "\n".join((presented.label, category_label(presented.category, locale), *wrapped))


def _canvas_settings(sources: list[dict[str, Any]]) -> ExoSettings:
    if not sources:
        return ExoSettings()
    width, height, _audio_width, _audio_height, wide_layout = _source_layout(sources[0])
    if wide_layout is not None:
        width, height = wide_layout.output_width, wide_layout.output_height
    recorded_rate = sources[0].get("frame_rate")
    probe_output = sources[0].get("stages", {}).get("source_probe", {}).get("output")
    if isinstance(probe_output, dict):
        recorded_rate = probe_output.get("frame_rate") or recorded_rate
    try:
        rate = max(1, round(float(recorded_rate)))
    except (TypeError, ValueError):
        rate = 60
    scale = height / DESIGN_HEIGHT
    return ExoSettings(
        width=width,
        height=height,
        rate=rate,
        font_size=max(1, round(60 * scale)),
        y_position=height * 0.33,
    )


def _source_layout(
    source: dict[str, Any],
) -> tuple[int, int, int, int, WideRecordingLayout | None]:
    probe_output = source.get("stages", {}).get("source_probe", {}).get("output")
    probe = probe_output if isinstance(probe_output, dict) else {}
    width = _positive_integer(probe.get("visual_width") or source.get("width"))
    height = _positive_integer(probe.get("visual_height") or source.get("height"))
    audio_width = _positive_integer(probe.get("audio_width") or source.get("audio_width"))
    audio_height = _positive_integer(probe.get("audio_height") or source.get("audio_height"))
    wide_layout = _wide_layout_from_dict(probe.get("wide_layout"))
    if width <= 0 or height <= 0:
        try:
            geometry = probe_video_geometry(Path(source["visual_path"]))
            width, height = geometry.width, geometry.height
            if source.get("media_mode") == "single" and wide_layout is None:
                wide_layout = analyze_wide_recording(Path(source["visual_path"]), geometry)
        except SubtitlerError:
            width, height = DESIGN_WIDTH, DESIGN_HEIGHT
    if (audio_width <= 0 or audio_height <= 0) and source.get("media_mode") == "paired":
        try:
            geometry = probe_video_geometry(Path(source["audio_path"]))
            audio_width, audio_height = geometry.width, geometry.height
        except SubtitlerError:
            pass
    if audio_width <= 0 or audio_height <= 0:
        audio_width, audio_height = width, height
    return width, height, audio_width, audio_height, wide_layout


def _wide_layout_from_dict(value: Any) -> WideRecordingLayout | None:
    if not isinstance(value, dict):
        return None
    try:
        return WideRecordingLayout(
            source_width=int(value["source_width"]),
            source_height=int(value["source_height"]),
            gameplay_width=int(value["gameplay_width"]),
            facecam_left=int(value["facecam_left"]),
            facecam_bottom=int(value["facecam_bottom"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _positive_integer(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return result if result > 0 else 0


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _emphasis_outline_color(value: Any) -> str:
    try:
        energy = max(-1.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        energy = 0.0
    if energy >= 0:
        return f"{round(255 * energy):02x}0000"
    return f"0000{round(255 * -energy):02x}"
