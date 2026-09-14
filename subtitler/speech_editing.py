"""Source-timed speech selection and display cleanup, independent of project runners."""

from __future__ import annotations

import unicodedata
from dataclasses import asdict
from typing import Any, Sequence

from .evidence import TranscriptEvidence
from .models import AlignedToken


EDITORIAL_SUBTITLE_TARGET_CHARS = 20
EDITORIAL_SUBTITLE_MAX_CHARS = 40
EDITORIAL_SUBTITLE_MIN_CHARS = 6


def tighten_transcript_to_speech(
    transcript: list[TranscriptEvidence],
    speech_activity: list[tuple[int, int]],
) -> list[TranscriptEvidence]:
    """Remove acoustic silence stretched into the outside of aligned text ranges."""
    if not speech_activity:
        return transcript
    tightened: list[TranscriptEvidence] = []
    for item in transcript:
        start_ms, end_ms = _tighten_range_to_speech_activity(
            item.start_ms,
            item.end_ms,
            speech_activity,
        )
        tightened.append(TranscriptEvidence(start_ms, end_ms, item.text))

    # Older cached transcription artifacts can contain a punctuation-only row
    # forced-aligned after several seconds of silence. Preserve its text while
    # attaching it to an adjacent spoken row instead of extending the range.
    result: list[TranscriptEvidence] = []
    pending_leading = ""
    for index, item in enumerate(tightened):
        acoustically_supported = any(
            speech_start < item.end_ms and speech_end > item.start_ms
            for speech_start, speech_end in speech_activity
        )
        if not _is_non_spoken_text(item.text) or acoustically_supported:
            text = f"{pending_leading}{item.text}" if pending_leading else item.text
            pending_leading = ""
            result.append(TranscriptEvidence(item.start_ms, item.end_ms, text))
            continue
        if result and not _is_non_spoken_text(result[-1].text):
            previous = result[-1]
            result[-1] = TranscriptEvidence(
                previous.start_ms,
                previous.end_ms,
                f"{previous.text}{item.text}",
            )
            continue
        if any(not _is_non_spoken_text(candidate.text) for candidate in tightened[index + 1 :]):
            pending_leading = f"{pending_leading}{item.text}"
        else:
            result.append(item)
    return result


def _tighten_range_to_speech_activity(
    start_ms: int,
    end_ms: int,
    speech_activity: list[tuple[int, int]],
) -> tuple[int, int]:
    overlapping = [
        (speech_start, speech_end)
        for speech_start, speech_end in speech_activity
        if speech_start < end_ms and speech_end > start_ms
    ]
    if not overlapping:
        return start_ms, end_ms
    tightened_start = max(start_ms, overlapping[0][0])
    tightened_end = min(end_ms, overlapping[-1][1])
    if tightened_end <= tightened_start:
        return start_ms, end_ms
    return tightened_start, tightened_end


def align_selected_phrases(
    phrases: Any,
    tokens: Sequence[AlignedToken],
    speech_activity: list[tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Verify verbatim phrase evidence and replace broad model ranges with token timing."""
    if not isinstance(phrases, list):
        return []
    rows = [asdict(token) for token in tokens if token.text]
    speech_activity = speech_activity or []
    stream_chars: list[str] = []
    char_tokens: list[int] = []
    for token_index, row in enumerate(rows):
        for character in str(row.get("text") or "").casefold():
            if character.isspace():
                continue
            stream_chars.append(character)
            char_tokens.append(token_index)
    stream = "".join(stream_chars)
    result: list[dict[str, Any]] = []
    for item in phrases:
        if not isinstance(item, dict):
            continue
        source_text = str(item.get("source_text") or "")
        needle, source_positions = _normalized_character_positions(source_text)
        if not needle:
            continue
        candidates: list[int] = []
        start = stream.find(needle)
        while start >= 0:
            candidates.append(start)
            start = stream.find(needle, start + 1)
        if not candidates:
            continue
        proposed_mid = (int(item.get("start_ms", 0)) + int(item.get("end_ms", 0))) // 2
        def distance(position: int) -> float:
            token = rows[char_tokens[position]]
            try:
                return abs(float(token["start"]) * 1000 - proposed_mid)
            except (KeyError, TypeError, ValueError):
                return float("inf")
        match = min(candidates, key=distance)
        aligned_segments: list[dict[str, Any]] = []
        for character_start, character_end in _subtitle_character_ranges(
            needle,
            match=match,
            char_tokens=char_tokens,
        ):
            first_index = char_tokens[match + character_start]
            last_index = char_tokens[match + character_end - 1]
            timing = _bounded_emphasis_timing(rows, first_index, last_index)
            if timing is None:
                continue
            start_ms, end_ms = _tighten_range_to_speech_activity(
                timing[0],
                timing[1],
                speech_activity,
            )
            if end_ms <= start_ms:
                continue
            chunk_text = _source_text_character_slice(
                source_text,
                source_positions,
                character_start,
                character_end,
            )
            if not chunk_text:
                continue
            normalized = dict(item)
            normalized.update(
                {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "timing_verified": True,
                    "source_text": chunk_text,
                    "text": chunk_text,
                    "parent_source_text": source_text,
                }
            )
            aligned_segments.append(normalized)
        segment_count = len(aligned_segments)
        for segment_index, normalized in enumerate(aligned_segments, 1):
            normalized["display_segment_index"] = segment_index
            normalized["display_segment_count"] = segment_count
            if normalized.get("id") and segment_count > 1:
                normalized["id"] = f"{normalized['id']}-{segment_index}"
            result.append(normalized)
    return _deduplicate_emphasized_phrases(result)


def _normalized_character_positions(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    source_positions: list[int] = []
    for source_index, character in enumerate(value):
        for normalized in character.casefold():
            if normalized.isspace():
                continue
            characters.append(normalized)
            source_positions.append(source_index)
    return "".join(characters), source_positions


def _subtitle_character_ranges(
    value: str,
    *,
    match: int,
    char_tokens: list[int],
) -> list[tuple[int, int]]:
    """Split one selected thought into short, token-timed, single-line beats."""
    length = len(value)
    if length <= EDITORIAL_SUBTITLE_TARGET_CHARS:
        return [(0, length)] if length else []
    strong_breaks = set(".!?。！？")
    soft_breaks = set(",、，:：;；…—-")
    token_boundaries = {
        index
        for index in range(1, length)
        if char_tokens[match + index - 1] != char_tokens[match + index]
    }
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while length - cursor > EDITORIAL_SUBTITLE_TARGET_CHARS:
        target = min(length, cursor + EDITORIAL_SUBTITLE_TARGET_CHARS)
        maximum = min(length, cursor + EDITORIAL_SUBTITLE_MAX_CHARS)
        minimum = min(length, cursor + EDITORIAL_SUBTITLE_MIN_CHARS)
        semantic_before = [
            index
            for index in range(minimum, target + 1)
            if value[index - 1] in strong_breaks | soft_breaks
        ]
        boundaries_before = [
            index
            for index in token_boundaries
            if minimum <= index <= target
        ]
        semantic_after = [
            index
            for index in range(target + 1, maximum + 1)
            if value[index - 1] in strong_breaks | soft_breaks
        ]
        boundaries_after = [
            index
            for index in token_boundaries
            if target < index <= maximum
        ]
        if semantic_before:
            end = semantic_before[-1]
        elif boundaries_before:
            end = boundaries_before[-1]
        elif semantic_after:
            end = semantic_after[0]
        elif boundaries_after:
            end = boundaries_after[0]
        else:
            end = target
        if length - end < EDITORIAL_SUBTITLE_MIN_CHARS and length - cursor <= EDITORIAL_SUBTITLE_MAX_CHARS:
            end = length
        if end <= cursor:
            end = min(length, cursor + EDITORIAL_SUBTITLE_TARGET_CHARS)
        ranges.append((cursor, end))
        cursor = end
    if cursor < length:
        ranges.append((cursor, length))
    return ranges


def _source_text_character_slice(
    value: str,
    source_positions: list[int],
    start: int,
    end: int,
) -> str:
    if not source_positions or start >= end:
        return ""
    raw_start = source_positions[start]
    raw_end = source_positions[end] if end < len(source_positions) else len(value)
    return " ".join(value[raw_start:raw_end].split())


def clean_selected_subtitles(
    phrases: list[dict[str, Any]],
    refiner: Any,
    *,
    batch_size: int = 64,
) -> list[dict[str, Any]]:
    """Clean only the verified phrases selected for the final editorial track."""
    cleaned_phrases: list[dict[str, Any]] = []
    for start in range(0, len(phrases), max(1, batch_size)):
        batch = phrases[start : start + max(1, batch_size)]
        originals = [str(item.get("text") or "").strip() for item in batch]
        refined = refiner.refine(originals)
        if not isinstance(refined, list) or len(refined) != len(originals):
            refined = originals
        for item, original, cleaned in zip(batch, originals, refined):
            normalized = dict(item)
            cleaned_text = " ".join(str(cleaned).split()) or original
            if len("".join(cleaned_text.split())) > EDITORIAL_SUBTITLE_MAX_CHARS:
                cleaned_text = original
            normalized["text"] = cleaned_text
            normalized["cleanup_applied"] = True
            cleaned_phrases.append(normalized)
    return cleaned_phrases


def _bounded_emphasis_timing(
    rows: list[dict[str, str]], first_index: int, last_index: int
) -> tuple[int, int] | None:
    """Keep trustworthy token timing while rejecting silence-stretched phrases."""
    selected = rows[first_index:last_index + 1]
    parsed: list[tuple[float, float, str]] = []
    try:
        for row in selected:
            start = float(row["start"])
            end = float(row["end"])
            text = str(row.get("text") or "")
            if end < start:
                return None
            parsed.append((start, end, text))
    except (KeyError, TypeError, ValueError):
        return None
    spoken = [
        index
        for index, (_, _, text) in enumerate(parsed)
        if any(not _is_punctuation(character) for character in text if not character.isspace())
    ]
    if not spoken:
        return None
    first_spoken = spoken[0]
    last_spoken = spoken[-1]
    for index in spoken[1:-1]:
        start, end, text = parsed[index]
        if end - start > _emphasis_token_limit(text, internal=True):
            return None
    for left, right in zip(spoken, spoken[1:]):
        if parsed[right][0] - parsed[left][1] > 1.25:
            return None
    first_start, first_end, first_text = parsed[first_spoken]
    last_start, last_end, last_text = parsed[last_spoken]
    start = max(first_start, first_end - _emphasis_token_limit(first_text, internal=False))
    end = min(last_end, last_start + _emphasis_token_limit(last_text, internal=False))
    if end <= start:
        return None
    return round(start * 1000), round(end * 1000)


def _emphasis_token_limit(text: str, *, internal: bool) -> float:
    visible = sum(not character.isspace() and not _is_punctuation(character) for character in text)
    per_character = 0.45 if internal else 0.22
    floor = 1.5 if internal else 0.75
    return max(floor, visible * per_character)


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith(("P", "S"))


def _is_non_spoken_text(text: str) -> bool:
    visible = [character for character in text if not character.isspace()]
    return bool(visible) and all(_is_punctuation(character) for character in visible)


def _deduplicate_emphasized_phrases(
    phrases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in sorted(
        phrases,
        key=lambda value: (
            int(value.get("start_ms", 0)),
            int(value.get("end_ms", 0)),
            -len(str(value.get("text") or "")),
        ),
    ):
        key = _emphasis_text_key(item.get("text"))
        if not key:
            continue
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(selected)
                if abs(int(existing.get("start_ms", 0)) - int(item.get("start_ms", 0))) <= 1500
                and _emphasis_texts_overlap(key, _emphasis_text_key(existing.get("text")))
            ),
            None,
        )
        if duplicate_index is None:
            selected.append(item)
            continue
        existing = selected[duplicate_index]
        if _emphasis_preference(item) > _emphasis_preference(existing):
            selected[duplicate_index] = item
    return sorted(selected, key=lambda value: (int(value["start_ms"]), int(value["end_ms"])))


def _emphasis_text_key(value: Any) -> str:
    return "".join(
        character.casefold()
        for character in str(value or "")
        if not character.isspace() and not _is_punctuation(character)
    )


def _emphasis_texts_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    shorter, longer = sorted((left, right), key=len)
    return shorter == longer or (len(shorter) >= 6 and shorter in longer)


def _emphasis_preference(item: dict[str, Any]) -> tuple[int, float, int]:
    text = str(item.get("text") or "")
    return (
        len(_emphasis_text_key(text)),
        float(item.get("confidence") or 0.0),
        len(text),
    )


