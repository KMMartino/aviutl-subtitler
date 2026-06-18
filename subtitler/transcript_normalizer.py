"""Adapters from normalized backend output to current subtitle-planner models."""

from __future__ import annotations

from .models import AlignedChunk, AlignedToken, AudioChunk, ExoMarker
from .transcription_backend import BackendTranscriptResult, SpeechRegion


def backend_result_to_aligned_chunks(result: BackendTranscriptResult) -> list[AlignedChunk]:
    chunks: list[AlignedChunk] = []
    regions = {region.index: region for region in result.speech_regions}
    for segment in sorted(result.segments, key=lambda item: (item.start, item.end, item.index)):
        if not segment.text.strip():
            continue
        region = regions.get(segment.index)
        audio_chunk = AudioChunk(
            index=segment.index,
            start=segment.start,
            end=segment.end,
            samples=None,
            wav_path=None,
            vad_activation=float(region.activation or 0.0) if region is not None else 0.0,
            vad_peak=float(region.peak or 0.0) if region is not None else 0.0,
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
