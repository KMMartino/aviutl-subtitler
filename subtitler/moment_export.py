"""Timestamp reports and grouped AviUtl media excerpts from one authoritative selection."""

from __future__ import annotations

import html
import math
import re
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any

from .exo import generate_exo_audio_object, generate_exo_file, generate_exo_object, generate_exo_video_object, write_exo
from .media_layout import cover_placement, top_right_overlay_placement, wide_recording_placements
from .models import ExoMediaSegment, ExoSettings
from .source_inspection import SourceInspection


def clock_ms(value: int) -> str:
    seconds, ms = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{ms:03}"


def export_moments(path: Path, artifact: dict[str, Any], inspection: SourceInspection,
                   media: list[dict[str, Any]], fps: int = 60) -> None:
    geometry = inspection.visual_geometry
    wide = inspection.wide_layout
    width = wide.output_width if wide else geometry.width if geometry else 1920
    height = wide.output_height if wide else geometry.height if geometry else 1080
    settings = ExoSettings(width=width, height=height, rate=fps)
    total_frames = 0
    objects: list[str] = []
    paired = len(media) == 2
    primary = cover_placement(width, height, geometry.width if geometry else width, geometry.height if geometry else height)
    overlay = None
    if wide:
        primary, overlay = wide_recording_placements(wide)
    elif paired and inspection.audio_geometry:
        overlay = top_right_overlay_placement(width, height, inspection.audio_geometry.width, inspection.audio_geometry.height)

    for group, match in enumerate(artifact["matches"], 1):
        # Round inward at the project clock, so output never exceeds the selected scope.
        start_sec = math.ceil(match["start_ms"] * fps / 1000) / fps
        end_sec = math.floor(match["end_ms"] * fps / 1000) / fps
        length = int(round((end_sec - start_sec) * fps))
        if length <= 0:
            match["export_warning"] = "Passage is shorter than one project frame; see timestamp report."
            continue
        start, end = total_frames + 1, total_frames + length
        total_frames = end
        match["timeline_start_frame"], match["timeline_end_frame"] = start, end
        for index, source in enumerate(media):
            placement = primary if index == 0 else overlay
            segment = ExoMediaSegment(start, end, math.ceil(start_sec * source["fps"] - 1e-8) + 1, group)
            objects.append(generate_exo_video_object(len(objects), segment, str(source["path"]), layer=1 + index * 2,
                scale_percent=placement.scale_percent if placement else 100,
                x_position=placement.x if placement else 0, y_position=placement.y if placement else 0,
                crop=placement.crop if placement else None))
            if source.get("has_audio"):
                objects.append(generate_exo_audio_object(len(objects), segment, str(source.get("audio_path", source["path"])),
                    layer=2 + index * 2, source_start_seconds=start_sec - source.get("audio_offset_sec", 0)))
        if wide and not paired and overlay:
            source = media[0]
            segment = ExoMediaSegment(start, end, math.ceil(start_sec * source["fps"] - 1e-8) + 1, group)
            objects.append(generate_exo_video_object(len(objects), segment, str(source["path"]), layer=3,
                scale_percent=overlay.scale_percent, x_position=overlay.x, y_position=overlay.y, crop=overlay.crop))
            if source.get("has_audio"):
                objects.append(generate_exo_audio_object(len(objects), segment, str(source.get("audio_path", source["path"])),
                    layer=4, volume=0, source_start_seconds=start_sec - source.get("audio_offset_sec", 0)))
        marker_layer = 5 if paired or wide else 3
        origin = artifact.get("origin_offset_ms", 0)
        text = (f"{group}. {clock_ms(match['start_ms'] + origin)}–{clock_ms(match['end_ms'] + origin)}\n"
                f"Confidence: {match['confidence']} · Relevance: {match['relevance']}\n{match['explanation']}")
        if match.get("boundary_warning"):
            text += "\n" + match["boundary_warning"]
        # Several same-group cards preserve long explanations without exceeding EXO's text field.
        lines = [line for paragraph in text.splitlines() for line in textwrap.wrap(paragraph, width=48) or [""]]
        cards = ["\n".join(lines[i:i + 5]) for i in range(0, len(lines), 5)]
        for card_index, card in enumerate(cards):
            card_start = start + (length * card_index // len(cards))
            card_end = max(card_start, start + length * (card_index + 1) // len(cards) - 1)
            objects.append(generate_exo_object(len(objects), card_start, card_end, card, settings,
                layer=marker_layer + card_index, group_id=group, font_size=max(16, round(height / 45)),
                y_position=height * 0.23))
        detour_layer = marker_layer + len(cards)
        for index, detour in enumerate(match["detours"]):
            detour_start = max(start, start + math.floor((detour["start_ms"] / 1000 - start_sec) * fps))
            detour_end = min(end, start + math.ceil((detour["end_ms"] / 1000 - start_sec) * fps) - 1)
            if detour_end >= detour_start:
                text = "Manual cut: " + detour["explanation"]
                objects.append(generate_exo_object(len(objects), detour_start, detour_end, text[:450],
                    replace(settings, text_color="00FFFF"), layer=detour_layer + index, group_id=group,
                    font_size=max(16, round(height / 50)), y_position=-height * 0.35))
    header = generate_exo_file([], settings, max(1, total_frames) / fps, insert_initial_empty=False)
    header = re.sub(r"(?m)^length=\d+", f"length={max(1, total_frames)}", header)
    write_exo(path, header + "\n\n" + "\n\n".join(objects))


def write_moment_report(path: Path, artifact: dict[str, Any]) -> None:
    origin = artifact.get("origin_offset_ms", 0)
    rows = []
    for item in artifact["matches"]:
        notes = [item["explanation"], item.get("boundary_warning", ""), item.get("export_warning", "")]
        notes.extend("Manual cut: " + detour["explanation"] for detour in item["detours"])
        rows.append("<tr><td>" + html.escape(clock_ms(item["start_ms"] + origin) + "–" + clock_ms(item["end_ms"] + origin))
                    + "</td><td>" + html.escape(item["confidence"]) + "</td><td>" + html.escape(item["relevance"])
                    + "</td><td>" + "<p>" + "</p><p>".join(html.escape(note) for note in notes if note) + "</p></td></tr>")
    total = sum(item["end_ms"] - item["start_ms"] for item in artifact["matches"])
    evidence = {row["id"]: row for row in artifact["evidence"]}
    details = "".join("<details><summary>" + html.escape(item["id"]) + " supporting evidence</summary>"
                      + "".join("<p>" + html.escape(clock_ms(evidence[key]["start_ms"] + origin) + " - " + clock_ms(evidence[key]["end_ms"] + origin) + " | " + evidence[key]["kind"] + ": " + evidence[key]["text"]) + "</p>" for key in item["evidence_ids"])
                      + "</details>" for item in artifact["matches"])
    path.write_text("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>Extracted moments</title><style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
        "table{width:100%;border-collapse:collapse}td,th{padding:.7rem;text-align:left;border-bottom:1px solid #aaa;"
        "vertical-align:top}td:first-child{min-width:12rem}p,details{overflow-wrap:anywhere}.table{overflow:auto}</style>"
        "<h1>Extracted moments</h1><p>" + html.escape(artifact["query"]) + "</p><p>"
        + f"{len(rows)} excerpts · {clock_ms(total)} selected · API cost ${artifact['api_cost_usd']:.4f}</p>"
        + "<p>Scope: " + clock_ms(artifact["range_start_ms"] + origin) + "–" + clock_ms(artifact["range_end_ms"] + origin)
        + ". Times refer to the original recording/VOD; EXO uses the local media. Keep referenced files in place.</p>"
        + "<p>Visual results reflect sampled frames; brief events may be missed. Confidence and relevance are qualitative.</p>"
        + ("<p>" + html.escape(artifact["frame_rate_warning"]) + "</p>" if artifact.get("frame_rate_warning") else "")
        + ("<p>No matching moments found.</p>" if not rows else "")
        + "<div class='table'><table><thead><tr><th>Source time</th><th>Confidence</th><th>Relevance</th><th>Explanation</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>" + details + "</html>", encoding="utf-8")
