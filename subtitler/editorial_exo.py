"""Build the editable AviUtl lookup project for an editorial analysis."""

from __future__ import annotations

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
from .editorial_locale import locale_label
from .editorial_cutting import (
    VOICE_LEADING_HANDLE_MS,
    VOICE_TRAILING_HANDLE_MS,
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
    human_information = (
        artifact.get("editorial_map", {}).get("workflow") == "human_information"
    )

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
        boundaries = (
            [0, int(source["duration_ms"])]
            if human_information
            else _source_edit_boundaries(source, final_actions)
        )
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
        offset_seconds += duration_seconds

    subtitles.extend(_selected_editorial_subtitles(artifact, source_offsets))

    presented = presented_editorial_items(artifact)
    cutting_assistant = (
        artifact.get("editorial_map", {}).get("workflow") == "cutting_assistant"
    )
    event_marker_layers: list[list[ExoMarker]] = []
    if human_information:
        marker_layers = _human_information_marker_layers(artifact, source_offsets)
        utterance_markers = _utterance_reference_markers(artifact, source_offsets)
        reference_marker_layers = _nonoverlapping_marker_lanes(utterance_markers)
        event_marker_layers = _event_graph_marker_layers(artifact, source_offsets)
        number_markers = []
    elif cutting_assistant:
        marker_layers = _cutting_assistant_marker_layers(artifact, source_offsets)
        utterance_markers = _utterance_reference_markers(artifact, source_offsets)
        reference_marker_layers = _nonoverlapping_marker_lanes(utterance_markers)
        number_markers = []
    else:
        marker_layers = _editorial_marker_layers(artifact, source_offsets, presented)
        reference_marker_layers = []
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
        reference_marker_layers=reference_marker_layers,
        event_marker_layers=event_marker_layers,
        segment_number_markers=number_markers,
        additional_marker_font_scale=1.2,
    )
    write_exo(path, content)


def write_cut_applied_editorial_exo(
    path: Path, artifact: dict[str, Any], cuts: list[dict[str, Any]]
) -> None:
    """Write a compacted editorial EXO after the user has reviewed cut markers."""
    sources = sorted(artifact["sources"], key=lambda item: item["order"])
    settings = _canvas_settings(sources)
    clips: list[ExoCompositeMediaClip] = []
    output_cursor_frame = 1
    source_output_offsets_ms: dict[str, int] = {}
    cuts_by_source = {
        str(source["source_id"]): sorted(
            [
                item
                for item in cuts
                if str(item.get("source_id") or "") == str(source["source_id"])
            ],
            key=lambda item: int(item["start_ms"]),
        )
        for source in sources
    }
    output_cursor_ms = 0
    next_group_id = 1
    narration_actions = [
        item
        for item in artifact.get("editorial_map", {}).get("final_actions", [])
        if isinstance(item, dict)
        and str(item.get("action_type") or "")
        in {"narrated_summary", "narration_bridge"}
    ]

    for source in sources:
        source_id = str(source["source_id"])
        source_output_offsets_ms[source_id] = output_cursor_ms
        source_cuts = cuts_by_source[source_id]
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
                settings.width, settings.height, audio_width, audio_height
            )
        duration_ms = int(source["duration_ms"])
        boundaries = {0, duration_ms}
        for cut in source_cuts:
            boundaries.update((int(cut["start_ms"]), int(cut["end_ms"])))
        for action in narration_actions:
            if str(action.get("source_id") or "") == source_id:
                boundaries.update(
                    (
                        max(0, min(duration_ms, _integer(action.get("start_ms")))),
                        max(0, min(duration_ms, _integer(action.get("end_ms")))),
                    )
                )
        ordered = sorted(boundaries)
        retained_ms = 0
        for start_ms, end_ms in zip(ordered, ordered[1:]):
            if end_ms <= start_ms or any(
                start_ms >= int(cut["start_ms"]) and end_ms <= int(cut["end_ms"])
                for cut in source_cuts
            ):
                continue
            source_start_frame = time_to_frame(start_ms / 1000.0, settings.rate)
            source_end_exclusive = time_to_frame(end_ms / 1000.0, settings.rate)
            frame_count = max(1, source_end_exclusive - source_start_frame)
            segment = ExoMediaSegment(
                output_cursor_frame,
                output_cursor_frame + frame_count - 1,
                source_start_frame,
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
            output_cursor_frame += frame_count
            retained_ms += end_ms - start_ms
            next_group_id += 1
        output_cursor_ms += retained_ms

    narration_markers: list[ExoMarker] = []
    locale = str(artifact.get("output_locale") or "en")
    for action in narration_actions:
        source_id = str(action.get("source_id") or "")
        if source_id not in source_output_offsets_ms:
            continue
        start_ms = _integer(action.get("start_ms"))
        end_ms = _integer(action.get("end_ms"))
        source_cuts = cuts_by_source[source_id]
        shifted_start = (
            source_output_offsets_ms[source_id]
            + start_ms
            - _removed_before(source_cuts, start_ms)
        )
        shifted_end = (
            source_output_offsets_ms[source_id]
            + end_ms
            - _removed_before(source_cuts, end_ms)
        )
        if shifted_end > shifted_start:
            narration_markers.append(
                ExoMarker(
                    shifted_start / 1000.0,
                    shifted_end / 1000.0,
                    locale_label(locale, "NARRATION", "ナレーション"),
                )
            )
    duration_seconds = max(0.0, (output_cursor_frame - 1) / settings.rate)
    content = generate_exo_file(
        [],
        settings,
        duration_seconds,
        insert_initial_empty=False,
        composite_media_clips=clips,
        subtitle_background=False,
        additional_marker_layers=[narration_markers] if narration_markers else [],
        segment_number_markers=[],
        additional_marker_font_scale=1.2,
    )
    write_exo(path, content)


def _removed_before(cuts: list[dict[str, Any]], point_ms: int) -> int:
    return sum(
        max(0, min(point_ms, int(cut["end_ms"])) - int(cut["start_ms"]))
        for cut in cuts
        if int(cut["start_ms"]) < point_ms
    )


def _cutting_assistant_marker_layers(
    artifact: dict[str, Any], source_offsets: dict[str, float]
) -> list[list[ExoMarker]]:
    cuts: list[ExoMarker] = []
    narration: list[ExoMarker] = []
    locale = str(artifact.get("output_locale") or "en")
    for item in artifact.get("editorial_map", {}).get("confirmed_cuts", []):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        start = source_offsets[source_id] + max(0, _integer(item.get("start_ms"))) / 1000.0
        end = source_offsets[source_id] + max(
            _integer(item.get("start_ms")) + 1, _integer(item.get("end_ms"))
        ) / 1000.0
        cuts.append(
            ExoMarker(
                start,
                end,
                f"[CUT] {_cut_kind_label(item.get('candidate_kind'), locale)}",
            )
        )
    for item in artifact.get("editorial_map", {}).get("final_actions", []):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        start = source_offsets[source_id] + max(0, _integer(item.get("start_ms"))) / 1000.0
        end = source_offsets[source_id] + max(
            _integer(item.get("start_ms")) + 1, _integer(item.get("end_ms"))
        ) / 1000.0
        if str(item.get("action_type") or "") in {
            "narrated_summary",
            "narration_bridge",
        }:
            narration.append(
                ExoMarker(start, end, locale_label(locale, "NARRATION", "ナレーション"))
            )
    return [layer for layer in (cuts, narration) if layer]


def _human_information_marker_layers(
    artifact: dict[str, Any], source_offsets: dict[str, float]
) -> list[list[ExoMarker]]:
    cuts: list[ExoMarker] = []
    narration: list[ExoMarker] = []
    locale = str(artifact.get("output_locale") or "en")
    editorial_map = artifact.get("editorial_map", {})
    for item in editorial_map.get("confirmed_cuts", []):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        start_ms = max(0, _integer(item.get("start_ms")))
        end_ms = max(start_ms + 1, _integer(item.get("end_ms")))
        cuts.append(
            ExoMarker(
                source_offsets[source_id] + start_ms / 1000.0,
                source_offsets[source_id] + end_ms / 1000.0,
                "[CUT]",
            )
        )
    for item in editorial_map.get("final_actions", []):
        if not isinstance(item, dict) or str(item.get("action_type") or "") not in {
            "narrated_summary",
            "narration_bridge",
        }:
            continue
        source_id = str(item.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        start_ms = max(0, _integer(item.get("start_ms")))
        end_ms = max(start_ms + 1, _integer(item.get("end_ms")))
        direction = " ".join(str(item.get("instruction") or "").split())[:240]
        marker_text = locale_label(locale, "NARRATION", "ナレーション")
        if direction:
            marker_text += "\n" + "\n".join(
                textwrap.wrap(
                    direction,
                    width=40,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
            )
        narration.append(
            ExoMarker(
                source_offsets[source_id] + start_ms / 1000.0,
                source_offsets[source_id] + end_ms / 1000.0,
                marker_text,
            )
        )
    return [layer for layer in (cuts, narration) if layer]


def _event_graph_marker_layers(
    artifact: dict[str, Any], source_offsets: dict[str, float]
) -> list[list[ExoMarker]]:
    """Project rich stored context into two restrained editor-facing rows."""
    locale = str(artifact.get("output_locale") or "en")
    local_states: list[ExoMarker] = []
    primary_activities: list[ExoMarker] = []

    for source in sorted(artifact.get("sources", []), key=lambda item: item.get("order", 0)):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        offset = source_offsets[source_id]
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        graph = result.get("event_graph") if isinstance(result.get("event_graph"), dict) else {}
        for item in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
            if not isinstance(item, dict):
                continue
            label = _context_marker_label(
                item.get("observed_label")
                or item.get("visual_state")
                or item.get("visual_category")
            )
            if not label or label.casefold() in {"unknown", "unobserved"}:
                continue
            start_ms = max(0, _integer(item.get("start_ms")))
            end_ms = max(start_ms + 1, _integer(item.get("end_ms")))
            local_states.append(
                ExoMarker(
                    offset + start_ms / 1000.0,
                    offset + end_ms / 1000.0,
                    f'{locale_label(locale, "[State]", "[状況]")} {label}',
                )
            )
        primary_activities.extend(
            _primary_activity_markers(
                result.get("activity_episodes"),
                offset_seconds=offset,
                prefix=locale_label(locale, "[Activity]", "[活動]"),
            )
        )
    return [
        layer
        for layer in (
            _merge_adjacent_context_markers(local_states),
            _merge_adjacent_context_markers(primary_activities),
        )
        if layer
    ]


def _context_marker_label(value: Any) -> str:
    label = " ".join(str(value or "").split())
    for separator in ("。", ". ", "！", "! ", "？", "? "):
        if separator in label:
            label = label.split(separator, 1)[0]
            break
    return label[:64]


def _primary_activity_markers(
    values: Any, *, offset_seconds: float, prefix: str
) -> list[ExoMarker]:
    episode_values = values if isinstance(values, list) else []
    episodes = [
        item
        for item in episode_values
        if isinstance(item, dict)
        if _integer(item.get("level") or 1) == 1
        and _integer(item.get("end_ms")) > _integer(item.get("start_ms"))
        and _context_marker_label(item.get("label"))
    ]
    boundaries = sorted(
        {
            point
            for item in episodes
            for point in (
                max(0, _integer(item.get("start_ms"))),
                max(0, _integer(item.get("end_ms"))),
            )
        }
    )
    markers: list[ExoMarker] = []
    for start_ms, end_ms in zip(boundaries, boundaries[1:]):
        active = [
            item
            for item in episodes
            if _integer(item.get("start_ms")) < end_ms
            and _integer(item.get("end_ms")) > start_ms
        ]
        if not active:
            continue
        chosen = max(
            active,
            key=lambda item: (
                float(item.get("confidence") or 0.0),
                _integer(item.get("end_ms")) - _integer(item.get("start_ms")),
            ),
        )
        markers.append(
            ExoMarker(
                offset_seconds + start_ms / 1000.0,
                offset_seconds + end_ms / 1000.0,
                f"{prefix} {_context_marker_label(chosen.get('label'))}",
            )
        )
    return _merge_adjacent_context_markers(markers)


def _merge_adjacent_context_markers(markers: list[ExoMarker]) -> list[ExoMarker]:
    merged: list[ExoMarker] = []
    for marker in sorted(markers, key=lambda item: (item.start_time, item.end_time)):
        if (
            merged
            and merged[-1].text == marker.text
            and marker.start_time <= merged[-1].end_time + 0.001
        ):
            merged[-1].end_time = max(merged[-1].end_time, marker.end_time)
        else:
            merged.append(ExoMarker(marker.start_time, marker.end_time, marker.text))
    return merged


def _cut_kind_label(value: Any, locale: str) -> str:
    labels = {
        "silence": ("Silence", "無音"),
        "filler": ("Filler speech", "フィラー"),
        "unnecessary_speech": ("Unnecessary speech", "不要な発話"),
        "irrelevant_topic": ("Irrelevant topic", "無関係な話題"),
        "idle_or_admin": ("Waiting / administration", "待機・事務操作"),
    }
    english, japanese = labels.get(str(value or ""), ("Cut", "カット"))
    return locale_label(locale, english, japanese)


def _utterance_reference_markers(
    artifact: dict[str, Any], source_offsets: dict[str, float]
) -> list[ExoMarker]:
    markers: list[ExoMarker] = []
    locale = str(artifact.get("output_locale") or "en")
    ordinal = 1
    for source in sorted(artifact.get("sources", []), key=lambda item: item.get("order", 0)):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        if source_id not in source_offsets:
            continue
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        utterances = result.get("utterance_groups", [])
        cut_ranges = sorted(
            (
                max(0, _integer(cut.get("start_ms"))),
                max(0, _integer(cut.get("end_ms"))),
            )
            for cut in artifact.get("editorial_map", {}).get("confirmed_cuts", [])
            if isinstance(cut, dict)
            and str(cut.get("source_id") or "") == source_id
            and _integer(cut.get("end_ms")) > _integer(cut.get("start_ms"))
        )
        for item in utterances if isinstance(utterances, list) else []:
            if not isinstance(item, dict):
                continue
            start_ms = max(0, _integer(item.get("start_ms")))
            end_ms = max(start_ms + 1, _integer(item.get("end_ms")))
            # A generated cut preserves a short audio handle around speech. Make
            # that handle visibly belong to the adjacent utterance instead of
            # leaving an unexplained sliver between its guide and [CUT]. Stored
            # transcript timing remains untouched.
            for cut_start_ms, cut_end_ms in cut_ranges:
                if start_ms - VOICE_LEADING_HANDLE_MS <= cut_end_ms < start_ms:
                    start_ms = cut_end_ms
                if end_ms < cut_start_ms <= end_ms + VOICE_TRAILING_HANDLE_MS:
                    end_ms = cut_start_ms
            excerpt = " ".join(str(item.get("text") or "").split())
            prefix = locale_label(locale, f"Utterance {ordinal:04d}", f"発話 {ordinal:04d}")
            chunks = [excerpt[index : index + 850] for index in range(0, len(excerpt), 850)]
            if not chunks:
                chunks = [""]
            for chunk_index, chunk in enumerate(chunks, 1):
                chunk_prefix = (
                    prefix
                    if len(chunks) == 1
                    else f"{prefix} ({chunk_index}/{len(chunks)})"
                )
                full_text = f"{chunk_prefix}: {chunk}" if chunk else chunk_prefix
                wrapped = "\n".join(
                    textwrap.wrap(
                        full_text,
                        width=80,
                        break_long_words=True,
                        break_on_hyphens=False,
                    )
                )
                markers.append(
                    ExoMarker(
                        source_offsets[source_id] + start_ms / 1000.0,
                        source_offsets[source_id] + end_ms / 1000.0,
                        wrapped,
                    )
                )
            ordinal += 1
    return markers


def _nonoverlapping_marker_lanes(markers: list[ExoMarker]) -> list[list[ExoMarker]]:
    lanes: list[list[ExoMarker]] = []
    lane_ends: list[float] = []
    for marker in sorted(
        markers, key=lambda item: (item.start_time, item.end_time, item.text)
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


def _selected_editorial_subtitles(
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
            text = " ".join(text.split())
            result.append(
                Subtitle(
                    start,
                    end,
                    text,
                    outline_color=_emphasis_outline_color(item.get("emphasis_energy")),
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
        for operation in action.get("operation_ranges", []):
            if not isinstance(operation, dict) or str(operation.get("source_id") or "") != source_id:
                continue
            operation_start = max(0, min(duration_ms, _integer(operation.get("start_ms"))))
            operation_end = max(0, min(duration_ms, _integer(operation.get("end_ms"))))
            if operation_end > operation_start:
                values.update((operation_start, operation_end))
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
    return "\n".join((presented.label, _marker_action_label(presented, locale), *wrapped))


def _marker_action_label(presented: PresentedEditorialItem, locale: str) -> str:
    """Describe the exact timeline operation rather than its parent strategy."""
    operation_role = str(presented.item.get("operation_role") or "")
    if operation_role == "keep":
        return locale_label(locale, "KEEP RANGE", "残す区間")
    if operation_role == "remove":
        return locale_label(locale, "CUT", "カット")
    if operation_role == "reference":
        return locale_label(locale, "REFERENCE VISUAL", "参照映像")
    if operation_role == "move":
        return locale_label(locale, "MOVE RANGE", "移動区間")
    return category_label(presented.category, locale)


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
