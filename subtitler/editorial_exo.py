"""Build the editable AviUtl lookup project for an editorial analysis."""

from __future__ import annotations

import csv
import json
import subprocess
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
from .models import ExoCompositeMediaClip, ExoMarker, ExoMediaSegment, ExoSettings, Subtitle


def write_editorial_exo(path: Path, artifact: dict[str, Any]) -> None:
    """Write linked source media, transcript subtitles, and editorial markers."""
    sources = sorted(artifact["sources"], key=lambda item: item["order"])
    settings = _canvas_settings(sources)
    clips: list[ExoCompositeMediaClip] = []
    subtitles: list[Subtitle] = []
    source_offsets: dict[str, float] = {}
    offset_seconds = 0.0

    for index, source in enumerate(sources):
        duration_seconds = int(source["duration_ms"]) / 1000.0
        source_offsets[source["source_id"]] = offset_seconds
        start_frame = time_to_frame(offset_seconds, settings.rate)
        end_frame = max(start_frame, time_to_frame(offset_seconds + duration_seconds, settings.rate) - 1)
        clips.append(
            ExoCompositeMediaClip(
                video_path=Path(source["visual_path"]),
                audio_path=Path(source["audio_path"]),
                segment=ExoMediaSegment(start_frame, end_frame, 1, index + 1),
            )
        )
        if artifact.get("subtitle_mode", "full") == "full":
            subtitles.extend(_source_subtitles(source, offset_seconds))
        offset_seconds += duration_seconds

    if artifact.get("subtitle_mode", "full") == "emphasis":
        subtitles.extend(_emphasized_subtitles(artifact, source_offsets))

    marker_layers = _editorial_marker_layers(artifact, source_offsets)
    content = generate_exo_file(
        subtitles,
        settings,
        offset_seconds,
        insert_initial_empty=False,
        composite_media_clips=clips,
        subtitle_background=False,
        additional_marker_layers=marker_layers,
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
            result.append(Subtitle(start, end, text))
    return sorted(result, key=lambda item: (item.start_time, item.end_time))


def _editorial_marker_layers(
    artifact: dict[str, Any], source_offsets: dict[str, float]
) -> list[list[ExoMarker]]:
    layers: dict[str, list[ExoMarker]] = {name: [] for name in EDITORIAL_LAYER_ORDER}
    for presented in presented_editorial_items(artifact):
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
        layers[presented.category].append(
            ExoMarker(
                offset + start,
                offset + end,
                _human_marker_text(presented, str(artifact.get("output_locale", "en"))),
            )
        )
    return [layers[name] for name in EDITORIAL_LAYER_ORDER]


def _human_marker_text(presented: PresentedEditorialItem, locale: str = "en") -> str:
    summary = primary_suggestion(presented, locale)
    wrapped = textwrap.wrap(
        summary,
        width=34,
        break_long_words=True,
        break_on_hyphens=False,
        max_lines=4,
        placeholder="…",
    )
    return "\n".join((presented.label, category_label(presented.category, locale), *wrapped))


def _canvas_settings(sources: list[dict[str, Any]]) -> ExoSettings:
    if not sources:
        return ExoSettings()
    visual_path = Path(sources[0]["visual_path"])
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                str(visual_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        width = max(1, int(stream["width"]))
        height = max(1, int(stream["height"]))
        rate = round(_frame_rate(stream.get("avg_frame_rate")) or _frame_rate(stream.get("r_frame_rate")))
        return ExoSettings(width=width, height=height, rate=max(1, rate or 60), y_position=height * 0.33)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        recorded_rate = sources[0].get("frame_rate")
        try:
            rate = max(1, round(float(recorded_rate)))
        except (TypeError, ValueError):
            rate = 60
        return ExoSettings(rate=rate, y_position=480.0)


def _frame_rate(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    numerator, separator, denominator = value.partition("/")
    try:
        return float(numerator) / float(denominator) if separator else float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
