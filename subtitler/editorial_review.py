"""Deterministically apply user-reviewed long-stream cut markers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable, Protocol

from .editorial_project import load_editorial_checkpoint
from .errors import SubtitlerError
from .exo import encode_text_for_exo


MIN_REVIEWED_CUT_MS = 1
_OBJECT = re.compile(r"(?ms)^\[(\d+)\]\n(.*?)(?=^\[\d+\]\n|\Z)")


@dataclass(frozen=True)
class _TextMarker:
    object_index: int
    layer: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _FootageCandidate:
    candidate_id: str
    start: int
    end: int
    label: str
    source_id: str
    timestamp_ms: int


@dataclass(frozen=True)
class _NarrationReview:
    facts: tuple[tuple[str, tuple[str, ...]], ...]
    references: tuple[_FootageCandidate, ...]


class NarrationReviewProvider(Protocol):
    def complete_structured(
        self,
        prompt: str,
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...


NARRATION_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "selected_candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["facts", "selected_candidate_ids"],
    "additionalProperties": False,
}


def apply_reviewed_editorial_cuts(
    review_project: Path,
    *,
    checkpoint_path: Path | None = None,
    output_path: Path | None = None,
    narration_provider: NarrationReviewProvider | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Read exact ``[CUT]`` EXO markers and compact the reviewed project itself."""
    review_project = review_project.resolve()
    if review_project.suffix.casefold() == ".aup":
        raise SubtitlerError(
            "AUP is AviUtl's binary project format and cannot be imported safely. "
            "Export the reviewed project as EXO, keep it beside its editorial JSON, and drop that EXO instead."
        )
    if review_project.suffix.casefold() != ".exo":
        raise SubtitlerError("Reviewed editorial cuts must be supplied as an EXO file")
    try:
        text = review_project.read_text(encoding="shift_jis").replace("\r\n", "\n")
    except (OSError, UnicodeError) as exc:
        raise SubtitlerError(f"Could not read reviewed EXO {review_project}: {exc}") from exc
    _report_progress(progress, "Reading reviewed EXO and locating its editorial checkpoint…")
    checkpoint = _resolve_checkpoint(review_project, text, checkpoint_path)
    project = load_editorial_checkpoint(checkpoint)
    if project.get("editorial_map", {}).get("workflow") not in {
        "cutting_assistant",
        "human_information",
    }:
        raise SubtitlerError("The matching checkpoint does not contain reviewed cut guides")
    fps = _exo_fps(text)
    narration_markers = _narration_markers(text)
    marker_frames = _cut_marker_frames(text)
    marker_frames = [
        marker
        for marker in marker_frames
        if not any(_frame_ranges_overlap(marker, (item.start, item.end)) for item in narration_markers)
    ]
    cuts, ignored_short = _map_markers_to_sources(
        marker_frames, fps=fps, project=project
    )
    _report_progress(
        progress,
        f"Found {len(cuts)} reviewed cuts and {len(narration_markers)} narration ranges.",
    )
    output = (
        output_path.resolve()
        if output_path is not None
        else review_project.with_name(f"{review_project.stem}-cuts-applied.exo")
    )
    if output == review_project:
        raise SubtitlerError("The applied-cuts output must not overwrite the reviewed EXO")
    narration_reviews = _build_narration_reviews(
        narration_markers,
        project=project,
        fps=fps,
        provider=narration_provider,
        progress=progress,
    )
    report = output.with_suffix(".html")
    _write_narration_review_html(
        report,
        review_project=review_project,
        checkpoint=checkpoint,
        narration_markers=narration_markers,
        project=project,
        fps=fps,
        reviews=narration_reviews,
        progress=progress,
    )
    # Keep the user's narration markers concise and authoritative. Generated
    # facts belong in the companion report; only source-location references are
    # added to the EXO timeline.
    text = _append_footage_reference_markers(
        text,
        narration_markers=narration_markers,
        reviews=narration_reviews,
    )
    compacted = _compact_reviewed_exo(text, marker_frames)
    try:
        output.write_bytes(compacted.replace("\n", "\r\n").encode("shift_jis"))
    except (OSError, UnicodeError) as exc:
        raise SubtitlerError(f"Could not write applied-cuts EXO {output}: {exc}") from exc
    return {
        "review_project": str(review_project),
        "checkpoint": str(checkpoint),
        "output_path": str(output),
        "report_path": str(report),
        "cut_count": len(cuts),
        "removed_ms": sum(int(item["end_ms"]) - int(item["start_ms"]) for item in cuts),
        "ignored_short_count": ignored_short,
        "narration_brief_count": len(narration_markers),
        "narration_reference_count": sum(
            len(review.references) for review in narration_reviews.values()
        ),
    }


def _report_progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _resolve_checkpoint(
    review_project: Path, exo_text: str, explicit: Path | None
) -> Path:
    if explicit is not None:
        path = explicit.resolve()
        if not path.is_file():
            raise SubtitlerError(f"Editorial checkpoint not found: {path}")
        return path
    direct = review_project.with_suffix(".json")
    if direct.is_file():
        return direct
    names = {
        value.strip().casefold()
        for value in re.findall(r"(?m)^file=(.+)$", exo_text)
        if value.strip()
    }
    matches: list[Path] = []
    for path in sorted(review_project.parent.glob("*editorial.json"))[:50]:
        try:
            project = load_editorial_checkpoint(path)
        except SubtitlerError:
            continue
        expected = {
            str(source.get(key) or "").strip().casefold()
            for source in project.get("sources", [])
            if isinstance(source, dict)
            for key in ("visual_path", "audio_path")
        }
        expected.discard("")
        if expected and expected.issubset(names):
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SubtitlerError(
            "More than one editorial checkpoint matches this EXO. Keep the reviewed EXO "
            "with the same filename stem as its JSON checkpoint."
        )
    raise SubtitlerError(
        "Could not find the editorial JSON associated with this EXO. Keep both files "
        "in the same directory with the same filename stem."
    )


def _exo_fps(text: str) -> float:
    rate_match = re.search(r"(?m)^rate=(\d+)$", text)
    scale_match = re.search(r"(?m)^scale=(\d+)$", text)
    if rate_match is None or scale_match is None:
        raise SubtitlerError("Reviewed EXO is missing its frame-rate header")
    rate = int(rate_match.group(1))
    scale = int(scale_match.group(1))
    if rate <= 0 or scale <= 0:
        raise SubtitlerError("Reviewed EXO has an invalid frame rate")
    return rate / scale


def _cut_marker_frames(text: str) -> list[tuple[int, int]]:
    by_layer: dict[int, list[tuple[int, int, str]]] = {}
    for marker in _text_markers(text):
        by_layer.setdefault(marker.layer, []).append(
            (marker.start, marker.end, marker.text)
        )
    if not by_layer:
        return []
    labeled = {
        layer: values
        for layer, values in by_layer.items()
        if any(text.strip() == "[CUT]" for _, _, text in values)
    }
    if not labeled:
        return []
    ordered = sorted(labeled.items(), key=lambda item: (-len(item[1]), item[0]))
    if len(ordered) > 1 and len(ordered[0][1]) == len(ordered[1][1]):
        raise SubtitlerError("Reviewed EXO has more than one possible cut-marker layer")
    if len(ordered[0][1]) > 10_000:
        raise SubtitlerError("Reviewed EXO contains too many cut markers")
    return sorted(
        (start, end)
        for start, end, value in ordered[0][1]
        if value.strip() == "[CUT]"
    )


def _text_markers(text: str) -> list[_TextMarker]:
    markers: list[_TextMarker] = []
    for match in _OBJECT.finditer(text):
        body = match.group(2)
        if "_name=テキスト" not in body:
            continue
        encoded = re.search(r"(?m)^text=([0-9a-fA-F]+)$", body)
        if encoded is None:
            continue
        layer = _integer_field(body, "layer")
        start = _integer_field(body, "start")
        end = _integer_field(body, "end")
        if layer > 0 and start > 0 and end >= start:
            markers.append(
                _TextMarker(
                    object_index=int(match.group(1)),
                    layer=layer,
                    start=start,
                    end=end,
                    text=_decode_exo_text(encoded.group(1)),
                )
            )
    return markers


def _narration_markers(text: str) -> list[_TextMarker]:
    return [marker for marker in _text_markers(text) if _is_narration_text(marker.text)]


def _is_narration_text(value: str) -> bool:
    return _narration_header(value) is not None


def _narration_header(value: str) -> tuple[str, str] | None:
    """Return the marker keyword and optional inline direction."""
    first_line = value.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0].strip()
    match = re.match(
        r"^(?:\[\s*(narration|ナレーション)\s*\]|【\s*(narration|ナレーション)\s*】|(narration|ナレーション))(?=$|\s|[:：])(.*)$",
        first_line,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    keyword = next(group for group in match.groups()[:3] if group is not None)
    inline = match.group(4).strip().lstrip(":：").strip()
    return keyword, inline


def _frame_ranges_overlap(
    first: tuple[int, int], second: tuple[int, int]
) -> bool:
    return first[0] <= second[1] and first[1] >= second[0]


def _build_narration_reviews(
    narration_markers: list[_TextMarker],
    *,
    project: dict[str, Any],
    fps: float,
    provider: NarrationReviewProvider | None,
    progress: Callable[[str], None] | None = None,
) -> dict[int, _NarrationReview]:
    reviews: dict[int, _NarrationReview] = {}
    for index, marker in enumerate(narration_markers, start=1):
        _report_progress(
            progress,
            f"Preparing narration brief {index}/{len(narration_markers)}…",
        )
        reviews[marker.object_index] = _review_narration_marker(
            marker,
            project=project,
            fps=fps,
            provider=provider,
        )
    return reviews


def _review_narration_marker(
    marker: _TextMarker,
    *,
    project: dict[str, Any],
    fps: float,
    provider: NarrationReviewProvider | None,
) -> _NarrationReview:
    candidates = _footage_candidates(marker, project=project, fps=fps)
    if provider is None:
        selected = _evenly_spaced_candidates(candidates, limit=5)
        return _NarrationReview(
            tuple((candidate.label, (candidate.candidate_id,)) for candidate in selected),
            tuple(selected),
        )
    direction = _narration_direction(marker.text)
    context = _narration_context(marker, project=project, fps=fps)
    locale = str(project.get("output_locale") or "en")
    prompt = f"""Task: prepare one factual narration reference brief after a human reviewed its exact source range.

Output language: {locale}
Optional user direction: {direction or "(none; summarize the range neutrally)"}

Rules:
- The reviewed narration range and optional user direction are authoritative.
- Facts are memory aids for the creator, not a finished voice-over script.
- With no direction, give a concise chronological account of the important events in the range.
- With a direction, prioritize facts and footage that directly support it while retaining essential setup and endpoint context.
- Select 3-7 representative footage candidates when available. Prefer setup, meaningful changes, visually distinct evidence, and the endpoint; do not select every event.
- Every fact must cite supplied candidate IDs when visual evidence supports it. Do not invent IDs or events.
- Keep each fact cohesive and concise. Do not add editorial rationale, confidence, or instructions to the user.

Stored factual context:
{json.dumps(context, ensure_ascii=False, separators=(",", ":"))}

Available source-timed footage candidates:
{json.dumps([{"candidate_id": item.candidate_id, "start_frame": item.start, "end_frame": item.end, "label": item.label} for item in candidates], ensure_ascii=False, separators=(",", ":"))}

Completion: return only the required JSON object. selected_candidate_ids must come from the supplied candidates and be ordered chronologically.
"""
    raw = provider.complete_structured(
        prompt,
        max_tokens=4_096,
        operation="editorial_narration_review",
        response_schema=NARRATION_REVIEW_SCHEMA,
    )
    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Narration review returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise SubtitlerError("Narration review must return a JSON object")
    by_id = {item.candidate_id: item for item in candidates}
    selected_ids = [
        str(value)
        for value in parsed.get("selected_candidate_ids", [])
        if str(value) in by_id
    ][:7]
    selected = sorted(
        (by_id[value] for value in dict.fromkeys(selected_ids)),
        key=lambda item: (item.start, item.end),
    )
    if not selected and candidates:
        selected = _evenly_spaced_candidates(candidates, limit=5)
    facts: list[tuple[str, tuple[str, ...]]] = []
    for item in parsed.get("facts", []):
        if not isinstance(item, dict):
            continue
        value = " ".join(str(item.get("text") or "").split())
        evidence = tuple(
            str(candidate_id)
            for candidate_id in item.get("evidence_ids", [])
            if str(candidate_id) in by_id
        )
        if value:
            facts.append((value[:280], evidence[:4]))
        if len(facts) >= 8:
            break
    if not facts:
        facts = [(item.label, (item.candidate_id,)) for item in selected]
    return _NarrationReview(tuple(facts), tuple(selected))


def _footage_candidates(
    marker: _TextMarker, *, project: dict[str, Any], fps: float
) -> list[_FootageCandidate]:
    candidates: list[_FootageCandidate] = []
    source_by_id = {
        str(item.get("source_id") or ""): item
        for item in project.get("sources", [])
        if isinstance(item, dict)
    }
    for source_id, local_start, local_end, absolute_offset in _frame_span_source_intersections(
        marker.start, marker.end, fps=fps, project=project
    ):
        source = source_by_id.get(source_id, {})
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        graph = result.get("event_graph") if isinstance(result.get("event_graph"), dict) else {}
        for item in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
            if not isinstance(item, dict) or not _timed_item_overlaps(
                item, local_start, local_end
            ):
                continue
            start_ms = absolute_offset + max(local_start, int(item.get("start_ms", 0)))
            end_ms = absolute_offset + min(local_end, int(item.get("end_ms", 0)))
            start_frame = max(marker.start, int(start_ms * fps / 1000.0) + 1)
            end_frame = min(marker.end, max(start_frame, int(end_ms * fps / 1000.0)))
            label = " ".join(_event_label(item).split())[:100]
            if label:
                candidates.append(
                    _FootageCandidate(
                        f"candidate-{len(candidates) + 1:04d}",
                        start_frame,
                        end_frame,
                        label,
                        source_id,
                        (max(local_start, int(item.get("start_ms", 0))) + min(local_end, int(item.get("end_ms", 0)))) // 2,
                    )
                )
    if not candidates:
        for source_id, local_start, local_end, absolute_offset in _frame_span_source_intersections(
            marker.start, marker.end, fps=fps, project=project
        ):
            source = source_by_id.get(source_id, {})
            result = source.get("result") if isinstance(source.get("result"), dict) else {}
            for item in result.get("activity_episodes", []):
                if not isinstance(item, dict) or not _timed_item_overlaps(
                    item, local_start, local_end
                ):
                    continue
                start_ms = absolute_offset + max(local_start, int(item.get("start_ms", 0)))
                end_ms = absolute_offset + min(local_end, int(item.get("end_ms", 0)))
                start_frame = max(marker.start, int(start_ms * fps / 1000.0) + 1)
                candidates.append(
                    _FootageCandidate(
                        f"candidate-{len(candidates) + 1:04d}",
                        start_frame,
                        min(
                            marker.end,
                            max(start_frame, int(end_ms * fps / 1000.0)),
                        ),
                        " ".join(str(item.get("label") or "Activity").split())[:100],
                        source_id,
                        (max(local_start, int(item.get("start_ms", 0))) + min(local_end, int(item.get("end_ms", 0)))) // 2,
                    )
                )
    return candidates[:240]


def _evenly_spaced_candidates(
    candidates: list[_FootageCandidate], *, limit: int
) -> list[_FootageCandidate]:
    if len(candidates) <= limit:
        return list(candidates)
    indices = {
        round(index * (len(candidates) - 1) / max(1, limit - 1))
        for index in range(limit)
    }
    return [candidates[index] for index in sorted(indices)]


def _narration_context(
    marker: _TextMarker, *, project: dict[str, Any], fps: float
) -> dict[str, Any]:
    intersections = _frame_span_source_intersections(
        marker.start, marker.end, fps=fps, project=project
    )
    source_by_id = {
        str(item.get("source_id") or ""): item
        for item in project.get("sources", [])
        if isinstance(item, dict)
    }
    activities: list[dict[str, Any]] = []
    topics: list[dict[str, Any]] = []
    for source_id, local_start, local_end, _ in intersections:
        result = source_by_id.get(source_id, {}).get("result", {})
        if not isinstance(result, dict):
            continue
        for item in result.get("activity_episodes", []):
            if isinstance(item, dict) and _timed_item_overlaps(
                item, local_start, local_end
            ):
                activities.append(
                    {
                        "start_ms": item.get("start_ms"),
                        "end_ms": item.get("end_ms"),
                        "label": item.get("label"),
                        "summary": item.get("summary"),
                    }
                )
        for item in result.get("semantic_spans", []):
            if isinstance(item, dict) and _timed_item_overlaps(
                item, local_start, local_end
            ):
                topics.append(
                    {
                        "start_ms": item.get("start_ms"),
                        "end_ms": item.get("end_ms"),
                        "label": item.get("label"),
                        "summary": item.get("summary"),
                    }
                )
    return {
        "title_or_game": project.get("title_or_game"),
        "objective": project.get("objective"),
        "activities": activities[:80],
        "spoken_topics": topics[:120],
        "related_threads": _overlapping_thread_lines(project, intersections)[:12],
    }


def _narration_direction(value: str) -> str:
    lines = [
        line.strip()
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    header = _narration_header(value)
    if header is None:
        return ""
    content = [header[1], *[line for line in lines[1:] if line]]
    content = [line for line in content if line]
    if not content:
        return ""
    generated_headers = {
        "what happens in this range:",
        "範囲内の出来事:",
        "related story context:",
        "関連する流れ:",
        "footage references:",
        "素材候補:",
    }
    first = content[0]
    lowered = first.casefold()
    body: list[str]
    if lowered.startswith("direction:"):
        body = [first.split(":", 1)[1].strip(), *content[1:]]
    elif first.startswith("方針:") or first.startswith("方針："):
        body = [re.split("[:：]", first, maxsplit=1)[1].strip(), *content[1:]]
    else:
        body = content
    if lowered in generated_headers or first.startswith(("- ", "・", "[N")):
        return ""
    retained: list[str] = []
    for line in body:
        if line.casefold() in generated_headers or line.startswith(("- ", "・", "[N")):
            break
        retained.append(line)
    return " ".join(retained)[:500]


def _write_narration_review_html(
    path: Path,
    *,
    review_project: Path,
    checkpoint: Path,
    narration_markers: list[_TextMarker],
    project: dict[str, Any],
    fps: float,
    reviews: dict[int, _NarrationReview],
    progress: Callable[[str], None] | None = None,
) -> None:
    locale = str(project.get("output_locale") or "en").casefold()
    japanese = locale.startswith("ja")
    title = "ナレーション資料" if japanese else "Narration briefs"
    empty = "ナレーション範囲はありません。" if japanese else "No narration ranges were found."
    direction_label = "方針" if japanese else "Direction"
    facts_label = "範囲内の出来事" if japanese else "What happens in this range"
    footage_label = "素材候補" if japanese else "Footage references"
    image_urls = _write_narration_reference_images(
        path,
        project=project,
        narration_markers=narration_markers,
        reviews=reviews,
        progress=progress,
    )
    cards: list[str] = []
    for index, marker in enumerate(narration_markers, start=1):
        review = reviews.get(marker.object_index, _NarrationReview((), ()))
        direction = _narration_direction(marker.text)
        facts = "".join(
            f"<li>{escape(fact)}{_html_evidence(evidence, review.references)}</li>"
            for fact, evidence in review.facts
        ) or "<li>—</li>"
        references = "".join(
            f'<li><div class="reference-image{"" if image_urls.get((marker.object_index, candidate.candidate_id)) else " placeholder"}">{_narration_reference_image(image_urls.get((marker.object_index, candidate.candidate_id)), candidate.label, japanese)}</div>'
            f'<div class="reference-copy"><strong>N{candidate_index}</strong> '
            f"{escape(_format_frame_time(candidate.start, fps))}–{escape(_format_frame_time(candidate.end, fps))} "
            f"<span>{escape(candidate.label)}</span></div></li>"
            for candidate_index, candidate in enumerate(review.references, start=1)
        ) or "<li>—</li>"
        direction_html = (
            f"<section><h3>{direction_label}</h3><p>{escape(direction)}</p></section>"
            if direction
            else ""
        )
        cards.append(
            f"<article><header><span>{index:03d}</span><strong>"
            f"{escape(_format_frame_time(marker.start, fps))}–{escape(_format_frame_time(marker.end, fps))}"
            f'</strong></header>{direction_html}<div class="narration-columns"><section class="events"><h3>{facts_label}</h3><ul>{facts}</ul></section>'
            f'<section class="references"><h3>{footage_label}</h3><ul>{references}</ul></section></div></article>'
        )
    document = f"""<!doctype html>
<html lang="{'ja' if japanese else 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark;background:#111820;color:#edf4fb;font:18px/1.55 system-ui,sans-serif}}body{{max-width:1400px;margin:0 auto;padding:40px 28px}}h1{{font-size:2rem;margin:0 0 8px}}.meta{{color:#9db0c2;font-size:.84rem;overflow-wrap:anywhere;margin-bottom:30px}}article{{background:#18232e;border:1px solid #304355;border-radius:14px;padding:22px 26px;margin:18px 0}}article header{{display:flex;gap:18px;align-items:baseline;border-bottom:1px solid #304355;padding-bottom:12px}}article header span{{color:#72c5ff;font-weight:800}}.narration-columns{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:30px}}h3{{font-size:1rem;color:#b9d9f0;margin:18px 0 8px}}p,ul{{margin:0}}li+li{{margin-top:10px}}code{{color:#96d7ff}}.references ul{{list-style:none;padding:0}}.references li{{display:grid;grid-template-columns:minmax(180px,42%) minmax(0,1fr);gap:14px;align-items:start;background:#111a23;border-radius:10px;padding:10px}}.reference-image{{aspect-ratio:16/9;background:#0a1016;border-radius:7px;overflow:hidden}}.reference-image img{{display:block;width:100%;height:100%;object-fit:cover}}.reference-image.placeholder{{display:grid;place-items:center;color:#7890a3;font-size:.75rem}}.reference-copy strong,.reference-copy span{{display:block}}.reference-copy strong{{color:#72c5ff}}@media(max-width:850px){{.narration-columns{{grid-template-columns:1fr}}.references li{{grid-template-columns:minmax(150px,40%) minmax(0,1fr)}}}}@media(max-width:520px){{body{{padding:20px 12px}}article{{padding:16px}}.references li{{grid-template-columns:1fr}}}}
</style></head><body><h1>{title}</h1><div class="meta">EXO: {escape(str(review_project))}<br>Checkpoint: {escape(str(checkpoint))}</div>
{''.join(cards) if cards else f'<p>{empty}</p>'}</body></html>"""
    try:
        path.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise SubtitlerError(f"Could not write narration report {path}: {exc}") from exc


def _html_evidence(
    evidence: tuple[str, ...], references: tuple[_FootageCandidate, ...]
) -> str:
    reference_index = {
        candidate.candidate_id: index for index, candidate in enumerate(references, start=1)
    }
    labels = [f"N{reference_index[item]}" for item in evidence if item in reference_index]
    return f" <code>[{', '.join(labels)}]</code>" if labels else ""


def _write_narration_reference_images(
    report_path: Path,
    *,
    project: dict[str, Any],
    narration_markers: list[_TextMarker],
    reviews: dict[int, _NarrationReview],
    progress: Callable[[str], None] | None,
) -> dict[tuple[int, str], str]:
    directory = report_path.with_name(f"{report_path.stem}-frames")
    try:
        if directory.is_dir() and directory.resolve().parent == report_path.parent.resolve():
            shutil.rmtree(directory)
    except OSError:
        pass
    source_paths = {
        str(source.get("source_id") or ""): Path(str(source.get("visual_path") or ""))
        for source in project.get("sources", [])
        if isinstance(source, dict)
    }
    requests = [
        (marker.object_index, index, candidate)
        for marker in narration_markers
        for index, candidate in enumerate(
            reviews.get(marker.object_index, _NarrationReview((), ())).references,
            start=1,
        )
    ]
    if requests:
        _report_progress(progress, f"Extracting {len(requests)} narration reference image(s)…")
    urls: dict[tuple[int, str], str] = {}
    for request_index, (object_index, reference_index, candidate) in enumerate(requests, start=1):
        source_path = source_paths.get(candidate.source_id)
        if source_path is None or not source_path.is_file():
            continue
        target = directory / f"narration-{object_index:04d}-N{reference_index}.jpg"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{candidate.timestamp_ms / 1000.0:.3f}",
                    "-i",
                    str(source_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=720:-2:force_original_aspect_ratio=decrease",
                    "-q:v",
                    "4",
                    "-y",
                    str(target),
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
            if completed.returncode == 0 and target.is_file() and target.stat().st_size:
                urls[(object_index, candidate.candidate_id)] = f"{directory.name}/{target.name}"
        except (OSError, subprocess.SubprocessError):
            continue
        if request_index % 5 == 0 or request_index == len(requests):
            _report_progress(progress, f"Narration reference images: {request_index}/{len(requests)}")
    return urls


def _narration_reference_image(url: str | None, label: str, japanese: bool) -> str:
    if url:
        return f'<img src="{escape(url)}" alt="{escape(label)}" loading="lazy">'
    return escape("プレビューなし" if japanese else "Preview unavailable")


def _format_frame_time(frame: int, fps: float) -> str:
    total_seconds = max(0, int((frame - 1) / fps))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"




def _append_footage_reference_markers(
    text: str,
    *,
    narration_markers: list[_TextMarker],
    reviews: dict[int, _NarrationReview],
) -> str:
    blocks = {
        int(match.group(1)): match.group(0).rstrip("\n")
        for match in _OBJECT.finditer(text)
    }
    next_index = max(blocks, default=-1) + 1
    additions: list[str] = []
    for narration in narration_markers:
        template = blocks.get(narration.object_index)
        review = reviews.get(narration.object_index)
        if template is None or review is None:
            continue
        for reference_index, reference in enumerate(review.references, 1):
            clone = re.sub(
                rf"(?m)^\[{narration.object_index}(?P<suffix>(?:\.\d+)?)\]$",
                lambda match: f"[{next_index}{match.group('suffix')}]",
                template,
            )
            clone = re.sub(
                r"(?m)^start=\d+$", f"start={reference.start}", clone, count=1
            )
            clone = re.sub(
                r"(?m)^end=\d+$", f"end={reference.end}", clone, count=1
            )
            clone = re.sub(
                r"(?m)^layer=\d+$",
                f"layer={max(1, narration.layer - 1)}",
                clone,
                count=1,
            )
            clone = re.sub(r"(?m)^group=\d+\n", "", clone, count=1)
            clone = re.sub(
                r"(?m)^text=[0-9a-fA-F]+$",
                f"text={encode_text_for_exo(f'[N{reference_index}] {reference.label}')}",
                clone,
                count=1,
            )
            additions.append(clone)
            next_index += 1
    if not additions:
        return text
    return f"{text.rstrip()}\n" + "\n".join(additions) + "\n"




def _frame_span_source_intersections(
    start_frame: int,
    end_frame: int,
    *,
    fps: float,
    project: dict[str, Any],
) -> list[tuple[str, int, int, int]]:
    global_start = round((start_frame - 1) * 1000.0 / fps)
    global_end = round(end_frame * 1000.0 / fps)
    result: list[tuple[str, int, int, int]] = []
    offset = 0
    for source in sorted(project.get("sources", []), key=lambda item: item.get("order", 0)):
        duration = int(source.get("duration_ms", 0))
        source_end = offset + duration
        if global_start < source_end and global_end > offset:
            result.append(
                (
                    str(source.get("source_id") or ""),
                    max(0, global_start - offset),
                    min(duration, global_end - offset),
                    offset,
                )
            )
        offset = source_end
    return result


def _timed_item_overlaps(item: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    return int(item.get("start_ms", 0)) < end_ms and int(item.get("end_ms", 0)) > start_ms


def _event_label(item: dict[str, Any]) -> str:
    return str(
        item.get("observed_label")
        or item.get("label")
        or item.get("visual_state")
        or item.get("visual_category")
        or item.get("semantic_label")
        or item.get("event_type")
        or ""
    )




def _overlapping_thread_lines(
    project: dict[str, Any],
    intersections: list[tuple[str, int, int, int]],
) -> list[str]:
    lines: list[str] = []
    for thread in project.get("editorial_map", {}).get("global_threads", []):
        if not isinstance(thread, dict):
            continue
        anchors = thread.get("anchors") if isinstance(thread.get("anchors"), list) else []
        if any(
            str(anchor.get("source_id") or "") == source_id
            and _timed_item_overlaps(anchor, start_ms, end_ms)
            for source_id, start_ms, end_ms, _ in intersections
            for anchor in anchors
            if isinstance(anchor, dict)
        ):
            title = " ".join(str(thread.get("title") or "").split())
            summary = " ".join(str(thread.get("summary") or "").split())
            value = f"{title}: {summary}" if title and summary else title or summary
            if value and value not in lines:
                lines.append(value)
    return lines




def _compact_reviewed_exo(
    text: str, marker_frames: list[tuple[int, int]]
) -> str:
    """Remove reviewed frame ranges while preserving and shifting every EXO object."""
    cuts = _merge_frame_ranges(marker_frames)
    if not cuts:
        return text if text.endswith("\n") else f"{text}\n"
    first_object = _OBJECT.search(text)
    if first_object is None:
        raise SubtitlerError("Reviewed EXO contains no timeline objects")
    header = text[: first_object.start()]
    length_match = re.search(r"(?m)^length=(\d+)$", header)
    if length_match is None:
        raise SubtitlerError("Reviewed EXO is missing its timeline length")
    original_length = int(length_match.group(1))
    removed_frames = sum(end - start + 1 for start, end in cuts)
    header = re.sub(
        r"(?m)^length=\d+$",
        f"length={max(0, original_length - removed_frames)}",
        header,
    )
    objects: list[str] = []
    next_index = 0
    group_ids: dict[tuple[int, int], int] = {}
    next_group = 1
    for match in _OBJECT.finditer(text):
        old_index = int(match.group(1))
        body = match.group(2).rstrip("\n")
        if _is_exact_cut_object(body):
            continue
        start = _integer_field(body, "start")
        end = _integer_field(body, "end")
        if start <= 0 or end < start:
            continue
        old_group = _integer_field(body, "group")
        for piece_ordinal, (piece_start, piece_end) in enumerate(
            _subtract_frame_ranges(start, end, cuts)
        ):
            shifted_start = piece_start - _frames_removed_before(piece_start, cuts)
            shifted_end = piece_end - _frames_removed_before(piece_end, cuts)
            piece = re.sub(r"(?m)^start=\d+$", f"start={shifted_start}", body, count=1)
            piece = re.sub(r"(?m)^end=\d+$", f"end={shifted_end}", piece, count=1)
            if old_group > 0:
                group_key = (old_group, piece_ordinal)
                if group_key not in group_ids:
                    group_ids[group_key] = next_group
                    next_group += 1
                piece = re.sub(
                    r"(?m)^group=\d+$", f"group={group_ids[group_key]}", piece, count=1
                )
            piece = _advance_media_source(piece, piece_start - start)
            piece = re.sub(
                rf"(?m)^\[{old_index}(?P<suffix>(?:\.\d+)?)\]$",
                lambda found: f"[{next_index}{found.group('suffix')}]",
                piece,
            )
            objects.append(f"[{next_index}]\n{piece}")
            next_index += 1
    return f"{header.rstrip()}\n" + "\n".join(objects) + "\n"


def _is_exact_cut_object(body: str) -> bool:
    if "_name=テキスト" not in body:
        return False
    encoded = re.search(r"(?m)^text=([0-9a-fA-F]+)$", body)
    return encoded is not None and _decode_exo_text(encoded.group(1)).strip() == "[CUT]"


def _merge_frame_ranges(values: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(values):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_frame_ranges(
    start: int, end: int, cuts: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    cursor = start
    for cut_start, cut_end in cuts:
        if cut_end < cursor:
            continue
        if cut_start > end:
            break
        if cut_start > cursor:
            pieces.append((cursor, min(end, cut_start - 1)))
        cursor = max(cursor, cut_end + 1)
        if cursor > end:
            break
    if cursor <= end:
        pieces.append((cursor, end))
    return pieces


def _frames_removed_before(frame: int, cuts: list[tuple[int, int]]) -> int:
    return sum(
        max(0, min(frame - 1, end) - start + 1)
        for start, end in cuts
        if start < frame
    )


def _advance_media_source(body: str, frame_delta: int) -> str:
    if frame_delta <= 0 or "_name=動画ファイル" not in body:
        return body
    match = re.search(r"(?m)^再生位置=(-?\d+)$", body)
    if match is None:
        return body
    advanced = int(match.group(1)) + frame_delta
    return re.sub(r"(?m)^再生位置=-?\d+$", f"再生位置={advanced}", body, count=1)


def _decode_exo_text(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-16-le").split("\0", 1)[0]
    except (ValueError, UnicodeDecodeError):
        return ""


def _integer_field(body: str, name: str) -> int:
    match = re.search(rf"(?m)^{re.escape(name)}=(\d+)$", body)
    return int(match.group(1)) if match is not None else -1


def _map_markers_to_sources(
    marker_frames: list[tuple[int, int]], *, fps: float, project: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    sources = sorted(project["sources"], key=lambda item: item["order"])
    source_ranges: list[tuple[dict[str, Any], int, int]] = []
    cursor = 0
    for source in sources:
        end = cursor + int(source["duration_ms"])
        source_ranges.append((source, cursor, end))
        cursor = end
    mapped: list[dict[str, Any]] = []
    ignored_short = 0
    frame_ms = 1000.0 / fps
    for start_frame, end_frame in marker_frames:
        global_start = round((start_frame - 1) * frame_ms)
        global_end = round(end_frame * frame_ms)
        if global_end - global_start < MIN_REVIEWED_CUT_MS:
            ignored_short += 1
            continue
        intersections = [
            (source, offset, source_end)
            for source, offset, source_end in source_ranges
            if global_start < source_end and global_end > offset
        ]
        if not intersections:
            raise SubtitlerError(
                "A reviewed cut marker falls outside the associated recordings"
            )
        for source, offset, source_end in intersections:
            intersection_start = max(global_start, offset)
            intersection_end = min(global_end, source_end)
            if intersection_end <= intersection_start:
                continue
            mapped.append(
                {
                    "source_id": str(source["source_id"]),
                    "start_ms": intersection_start - offset,
                    "end_ms": intersection_end - offset,
                }
            )
    return _merge_reviewed_cuts(mapped), ignored_short


def _merge_reviewed_cuts(cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in sorted(
        cuts, key=lambda value: (str(value["source_id"]), int(value["start_ms"]))
    ):
        if (
            result
            and result[-1]["source_id"] == item["source_id"]
            and int(item["start_ms"]) <= int(result[-1]["end_ms"])
        ):
            result[-1]["end_ms"] = max(
                int(result[-1]["end_ms"]), int(item["end_ms"])
            )
        else:
            result.append(dict(item))
    return result
