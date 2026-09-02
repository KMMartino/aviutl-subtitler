"""Adapters from normalized backend output to current subtitle-planner models."""

from __future__ import annotations

import unicodedata

from .models import AlignedChunk, AlignedToken, AudioChunk, ExoMarker
from .transcription_backend import BackendTranscriptResult, RawVadSpeechInterval, SpeechRegion


def is_non_spoken_text(text: str) -> bool:
    """Return whether text consists only of punctuation/symbol characters."""
    visible = [character for character in text if not character.isspace()]
    return bool(visible) and all(
        unicodedata.category(character).startswith(("P", "S"))
        for character in visible
    )


def _speech_overlaps(
    start: float,
    end: float,
    speech_activity: list[RawVadSpeechInterval],
) -> list[RawVadSpeechInterval]:
    return [
        item for item in speech_activity if item.start < end and item.end > start
    ]


def _attach_unsupported_punctuation(
    chunks: list[AlignedChunk],
    speech_activity: list[RawVadSpeechInterval],
) -> None:
    """Collapse silence-aligned punctuation onto an adjacent spoken boundary."""
    if not speech_activity:
        return
    tokens = [token for chunk in chunks for token in chunk.tokens]
    spoken_indexes = [
        index for index, token in enumerate(tokens) if not is_non_spoken_text(token.text)
    ]
    for index, token in enumerate(tokens):
        if (
            not is_non_spoken_text(token.text)
            or _speech_overlaps(token.start, token.end, speech_activity)
        ):
            continue
        previous = next((candidate for candidate in reversed(spoken_indexes) if candidate < index), None)
        following = next((candidate for candidate in spoken_indexes if candidate > index), None)
        if previous is not None:
            spoken = tokens[previous]
            overlaps = _speech_overlaps(spoken.start, spoken.end, speech_activity)
            if overlaps:
                spoken.end = min(spoken.end, overlaps[-1].end)
            boundary = spoken.end
        elif following is not None:
            spoken = tokens[following]
            overlaps = _speech_overlaps(spoken.start, spoken.end, speech_activity)
            if overlaps:
                spoken.start = max(spoken.start, overlaps[0].start)
            boundary = spoken.start
        else:
            continue
        token.start = boundary
        token.end = boundary


def backend_result_to_aligned_chunks(result: BackendTranscriptResult) -> list[AlignedChunk]:
    chunks: list[AlignedChunk] = []
    regions = {region.index: region for region in result.speech_regions}
    for segment in sorted(result.segments, key=lambda item: (item.start, item.end, item.index)):
        if not segment.text.strip():
            continue
        region = regions.get(segment.index)
        segment_group_index = segment.metadata.get("vad_group_index")
        region_group_index = (
            region.metadata.get("vad_group_index") if region is not None else None
        )
        vad_group_index = (
            int(segment_group_index)
            if segment_group_index is not None
            else int(region_group_index)
            if region_group_index is not None
            else None
        )
        audio_chunk = AudioChunk(
            index=segment.index,
            start=segment.start,
            end=segment.end,
            samples=None,
            wav_path=None,
            vad_activation=float(region.activation or 0.0) if region is not None else 0.0,
            vad_peak=float(region.peak or 0.0) if region is not None else 0.0,
            vad_group_index=vad_group_index,
        )
        tokens = [
            AlignedToken(
                text=token.text,
                start=float(token.start),
                end=float(token.end),
                kind=token.kind,
            )
            for token in segment.tokens
            if token.text.strip() and token.start is not None and token.end is not None
        ]
        chunks.append(
            AlignedChunk(
                chunk=audio_chunk,
                text=segment.text,
                tokens=tokens,
                fallback=segment.fallback_timing or not tokens,
            )
        )
    _attach_unsupported_punctuation(chunks, result.raw_vad_speech_intervals)
    return chunks


def speech_regions_to_markers(regions: list[SpeechRegion]) -> list[ExoMarker]:
    return [
        ExoMarker(
            region.start,
            region.end,
            f"VAD {index} a={float(region.activation or 0.0):.2f}",
        )
        for index, region in enumerate(sorted(regions, key=lambda item: (item.start, item.end)), start=1)
    ]
