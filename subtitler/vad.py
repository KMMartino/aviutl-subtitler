"""Silero VAD segmentation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .audio import write_wav_segment
from .errors import VadError
from .models import AudioChunk


def _merge_speech_timestamps(
    timestamps: list[dict],
    sample_rate: int,
    max_chunk_sec: float,
    min_silence_ms: int,
    speech_pad_ms: int,
    total_samples: int,
) -> list[tuple[int, int]]:
    max_samples = int(max_chunk_sec * sample_rate)
    min_silence = int(min_silence_ms * sample_rate / 1000)
    pad = int(speech_pad_ms * sample_rate / 1000)
    merged: list[tuple[int, int]] = []

    for item in timestamps:
        start = max(0, int(item["start"]) - pad)
        end = min(total_samples, int(item["end"]) + pad)
        if not merged:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        silence = start - prev_end
        merged_duration = end - prev_start
        if merged_duration <= max_samples and silence <= min_silence:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def segment_speech(
    samples: Any,
    sample_rate: int,
    max_chunk_sec: float,
    min_speech_sec: float,
    min_silence_ms: int,
    speech_pad_ms: int,
    temp_dir: Path | None = None,
    keep_temp: bool = False,
) -> list[AudioChunk]:
    """Run Silero VAD and return speech chunks split only on detected silence."""
    try:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
    except ImportError as exc:
        raise VadError("silero-vad and torch are required for VAD") from exc

    if sample_rate != 16000:
        raise VadError("Silero VAD input must be 16 kHz")
    try:
        model = load_silero_vad()
        speech = get_speech_timestamps(
            torch.from_numpy(samples),
            model,
            sampling_rate=sample_rate,
            min_speech_duration_ms=int(min_speech_sec * 1000),
            max_speech_duration_s=max_chunk_sec,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        )
    except Exception as exc:
        raise VadError("Silero VAD failed") from exc

    spans = _merge_speech_timestamps(
        speech, sample_rate, max_chunk_sec, min_silence_ms, speech_pad_ms, len(samples)
    )
    chunks: list[AudioChunk] = []
    for index, (start_sample, end_sample) in enumerate(spans):
        chunk_samples = samples[start_sample:end_sample]
        if len(chunk_samples) == 0:
            continue
        wav_path = None
        if keep_temp and temp_dir is not None:
            wav_path = temp_dir / f"chunk_{index:05d}.wav"
            write_wav_segment(chunk_samples, sample_rate, wav_path)
        chunks.append(
            AudioChunk(
                index=index,
                start=start_sample / sample_rate,
                end=end_sample / sample_rate,
                samples=chunk_samples,
                wav_path=wav_path,
            )
        )
    return chunks


def split_chunk_with_tighter_vad(
    chunk: AudioChunk,
    sample_rate: int,
    temp_dir: Path | None = None,
    keep_temp: bool = False,
    min_piece_sec: float = 0.25,
) -> list[AudioChunk]:
    """Rerun VAD on one chunk with tighter settings, falling back to a quiet midpoint split."""
    duration = max(chunk.end - chunk.start, 0.0)
    if duration <= min_piece_sec * 2:
        return [chunk]

    attempts = [
        (max(duration / 2, min_piece_sec), 250, 80),
        (max(duration / 2, min_piece_sec), 150, 40),
        (max(duration / 3, min_piece_sec), 80, 20),
        (max(duration / 4, min_piece_sec), 40, 0),
    ]
    for max_chunk_sec, min_silence_ms, speech_pad_ms in attempts:
        try:
            local = segment_speech(
                samples=chunk.samples,
                sample_rate=sample_rate,
                max_chunk_sec=max_chunk_sec,
                min_speech_sec=min_piece_sec,
                min_silence_ms=min_silence_ms,
                speech_pad_ms=speech_pad_ms,
                temp_dir=None,
                keep_temp=False,
            )
        except VadError:
            continue
        adjusted = _offset_subchunks(chunk, local, sample_rate, temp_dir, keep_temp)
        if len(adjusted) >= 2:
            return adjusted

    return _quiet_midpoint_split(chunk, sample_rate, temp_dir, keep_temp, min_piece_sec)


def _offset_subchunks(
    parent: AudioChunk,
    local: list[AudioChunk],
    sample_rate: int,
    temp_dir: Path | None,
    keep_temp: bool,
) -> list[AudioChunk]:
    result: list[AudioChunk] = []
    parent_start_sample = int(round(parent.start * sample_rate))
    for index, sub in enumerate(local):
        start_sample = max(0, int(round(sub.start * sample_rate)))
        end_sample = min(len(parent.samples), int(round(sub.end * sample_rate)))
        if end_sample <= start_sample:
            continue
        wav_path = None
        if keep_temp and temp_dir is not None:
            wav_path = temp_dir / f"chunk_{parent.index:05d}_split_{index:02d}.wav"
            write_wav_segment(parent.samples[start_sample:end_sample], sample_rate, wav_path)
        result.append(
            AudioChunk(
                index=parent.index,
                start=(parent_start_sample + start_sample) / sample_rate,
                end=(parent_start_sample + end_sample) / sample_rate,
                samples=parent.samples[start_sample:end_sample],
                wav_path=wav_path,
            )
        )
    return result


def _quiet_midpoint_split(
    chunk: AudioChunk,
    sample_rate: int,
    temp_dir: Path | None,
    keep_temp: bool,
    min_piece_sec: float,
) -> list[AudioChunk]:
    import numpy as np

    total = len(chunk.samples)
    min_samples = max(1, int(min_piece_sec * sample_rate))
    if total < min_samples * 2:
        return [chunk]
    center = total // 2
    radius = max(min(total // 4, sample_rate * 5), min_samples)
    start = max(min_samples, center - radius)
    end = min(total - min_samples, center + radius)
    if end <= start:
        cut = center
    else:
        window = np.asarray(chunk.samples[start:end])
        frame = max(1, int(0.05 * sample_rate))
        if len(window) <= frame:
            cut = center
        else:
            energy = np.convolve(window * window, np.ones(frame, dtype=window.dtype), mode="valid")
            cut = start + int(np.argmin(energy)) + frame // 2

    pieces = []
    for index, (piece_start, piece_end) in enumerate(((0, cut), (cut, total))):
        if piece_end <= piece_start:
            continue
        wav_path = None
        if keep_temp and temp_dir is not None:
            wav_path = temp_dir / f"chunk_{chunk.index:05d}_split_{index:02d}.wav"
            write_wav_segment(chunk.samples[piece_start:piece_end], sample_rate, wav_path)
        pieces.append(
            AudioChunk(
                index=chunk.index,
                start=chunk.start + piece_start / sample_rate,
                end=chunk.start + piece_end / sample_rate,
                samples=chunk.samples[piece_start:piece_end],
                wav_path=wav_path,
            )
        )
    return pieces
