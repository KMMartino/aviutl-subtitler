"""Silero VAD segmentation."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Callable

from .audio import write_wav_segment
from .errors import VadError
from .models import AudioChunk


class VadSession:
    """One serialized Silero model instance shared by a pipeline run."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = threading.Lock()
        self.inference_count = 0

    def probabilities(
        self,
        samples: Any,
        sample_rate: int,
        progress_callback: Callable[[float], None] | None = None,
    ) -> tuple[list[float], int]:
        try:
            import torch
            from silero_vad import load_silero_vad
        except ImportError as exc:
            raise VadError("silero-vad and torch are required for VAD") from exc
        with self._lock:
            if self._model is None:
                self._model = load_silero_vad()
            self.inference_count += 1
            return _speech_probabilities(
                torch.from_numpy(samples), self._model, sample_rate, progress_callback
            )


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


def _speech_probabilities(
    audio_tensor: Any,
    model: Any,
    sample_rate: int,
    progress_callback: Callable[[float], None] | None = None,
) -> tuple[list[float], int]:
    import torch

    window_size_samples = 512 if sample_rate == 16000 else 256
    model.reset_states()
    probabilities: list[float] = []
    audio_length_samples = len(audio_tensor)
    for current_start_sample in range(0, audio_length_samples, window_size_samples):
        chunk = audio_tensor[current_start_sample : current_start_sample + window_size_samples]
        if len(chunk) < window_size_samples:
            chunk = torch.nn.functional.pad(chunk, (0, int(window_size_samples - len(chunk))))
        probabilities.append(float(model(chunk, sample_rate).item()))
        if progress_callback is not None:
            progress = min(audio_length_samples, current_start_sample + window_size_samples)
            progress_callback(progress / max(1, audio_length_samples) * 100.0)
    return probabilities, window_size_samples


def _span_activation(
    probabilities: list[float],
    window_size_samples: int,
    start_sample: int,
    end_sample: int,
) -> tuple[float, float]:
    if not probabilities or end_sample <= start_sample:
        return 0.0, 0.0
    first = max(0, start_sample // window_size_samples)
    last = min(len(probabilities) - 1, max(first, (end_sample - 1) // window_size_samples))
    values = probabilities[first : last + 1]
    if not values:
        return 0.0, 0.0
    return sum(values) / len(values), max(values)


def _speech_timestamps_from_probabilities(
    probabilities: list[float],
    window_size_samples: int,
    sample_rate: int,
    total_samples: int,
    max_chunk_sec: float,
    min_speech_sec: float,
    min_silence_ms: int,
    speech_pad_ms: int,
    threshold: float = 0.5,
) -> list[dict]:
    min_speech_samples = int(min_speech_sec * sample_rate)
    speech_pad_samples = int(speech_pad_ms * sample_rate / 1000)
    max_speech_samples = int(max_chunk_sec * sample_rate) - window_size_samples - 2 * speech_pad_samples
    max_speech_samples = max(window_size_samples, max_speech_samples)
    min_silence_samples = int(min_silence_ms * sample_rate / 1000)
    min_silence_at_max_speech = int(0.098 * sample_rate)
    neg_threshold = max(threshold - 0.15, 0.01)

    triggered = False
    current_start = 0
    temp_end = 0
    possible_ends: list[tuple[int, int]] = []
    speeches: list[dict] = []

    for index, probability in enumerate(probabilities):
        current_sample = min(total_samples, window_size_samples * index)

        if probability >= threshold and temp_end:
            silence_duration = current_sample - temp_end
            if silence_duration > min_silence_at_max_speech:
                possible_ends.append((temp_end, silence_duration))
            temp_end = 0

        if probability >= threshold and not triggered:
            triggered = True
            current_start = current_sample
            possible_ends = []
            continue

        if triggered and current_sample - current_start > max_speech_samples:
            if possible_ends:
                end_sample, silence_duration = max(possible_ends, key=lambda item: item[1])
                if end_sample - current_start >= min_speech_samples:
                    speeches.append({"start": current_start, "end": min(total_samples, end_sample)})
                current_start = min(total_samples, end_sample + silence_duration)
                temp_end = 0
                possible_ends = []
                if probability < threshold:
                    triggered = False
            else:
                end_sample = current_sample
                if end_sample - current_start >= min_speech_samples:
                    speeches.append({"start": current_start, "end": min(total_samples, end_sample)})
                current_start = current_sample
                temp_end = 0
                possible_ends = []
            continue

        if probability < neg_threshold and triggered:
            if not temp_end:
                temp_end = current_sample
            if current_sample - temp_end >= min_silence_samples:
                end_sample = temp_end
                if end_sample - current_start >= min_speech_samples:
                    speeches.append({"start": current_start, "end": min(total_samples, end_sample)})
                triggered = False
                temp_end = 0
                possible_ends = []

    if triggered and total_samples - current_start >= min_speech_samples:
        speeches.append({"start": current_start, "end": total_samples})
    return speeches


def segment_speech(
    samples: Any,
    sample_rate: int,
    max_chunk_sec: float,
    min_speech_sec: float,
    min_silence_ms: int,
    speech_pad_ms: int,
    temp_dir: Path | None = None,
    keep_temp: bool = False,
    progress_callback: Callable[[str, float], None] | None = None,
    session: VadSession | None = None,
) -> list[AudioChunk]:
    """Run Silero VAD and return speech chunks split only on detected silence."""
    try:
        import torch  # noqa: F401
    except ImportError as exc:
        raise VadError("silero-vad and torch are required for VAD") from exc

    if sample_rate != 16000:
        raise VadError("Silero VAD input must be 16 kHz")
    try:
        speech_probs, window_size_samples = (session or VadSession()).probabilities(
            samples,
            sample_rate,
            (lambda progress: progress_callback("inference", progress)) if progress_callback else None,
        )
        speech = _speech_timestamps_from_probabilities(
            speech_probs,
            window_size_samples,
            sample_rate,
            len(samples),
            max_chunk_sec,
            min_speech_sec,
            min_silence_ms,
            speech_pad_ms,
        )
    except Exception as exc:
        raise VadError("Silero VAD failed") from exc

    spans = _merge_speech_timestamps(
        speech, sample_rate, max_chunk_sec, min_silence_ms, speech_pad_ms, len(samples)
    )
    return _chunks_from_spans(
        spans,
        samples,
        sample_rate,
        speech_probs,
        window_size_samples,
        temp_dir,
        keep_temp,
    )


def segment_speech_with_groups(
    samples: Any,
    sample_rate: int,
    max_chunk_sec: float,
    min_speech_sec: float,
    min_silence_ms: int,
    speech_pad_ms: int,
    cleanup_group_max_sec: float,
    temp_dir: Path | None = None,
    keep_temp: bool = False,
    progress_callback: Callable[[str, float], None] | None = None,
    session: VadSession | None = None,
) -> tuple[list[AudioChunk], list[AudioChunk]]:
    """Run fine VAD and tag chunks with cleanup groups split at the largest gaps."""
    try:
        import torch  # noqa: F401
    except ImportError as exc:
        raise VadError("silero-vad and torch are required for VAD") from exc

    if sample_rate != 16000:
        raise VadError("Silero VAD input must be 16 kHz")
    try:
        speech_probs, window_size_samples = (session or VadSession()).probabilities(
            samples,
            sample_rate,
            (lambda progress: progress_callback("inference", progress)) if progress_callback else None,
        )
        fine_speech = _speech_timestamps_from_probabilities(
            speech_probs,
            window_size_samples,
            sample_rate,
            len(samples),
            max_chunk_sec,
            min_speech_sec,
            min_silence_ms,
            speech_pad_ms,
        )
    except Exception as exc:
        raise VadError("Silero VAD failed") from exc

    fine_spans = _merge_speech_timestamps(
        fine_speech, sample_rate, max_chunk_sec, min_silence_ms, speech_pad_ms, len(samples)
    )
    fine_chunks = _chunks_from_spans(
        fine_spans,
        samples,
        sample_rate,
        speech_probs,
        window_size_samples,
        temp_dir=temp_dir,
        keep_temp=keep_temp,
    )
    groups = assign_vad_groups_by_largest_gaps(fine_chunks, max_group_sec=max(cleanup_group_max_sec, max_chunk_sec))
    return fine_chunks, groups


def assign_vad_groups_by_largest_gaps(chunks: list[AudioChunk], max_group_sec: float) -> list[AudioChunk]:
    ordered = sorted(chunks, key=lambda item: (item.start, item.end, item.index))
    if not ordered:
        return []
    groups: list[list[AudioChunk]] = [ordered]
    while True:
        oversized = [
            (index, group[-1].end - group[0].start)
            for index, group in enumerate(groups)
            if len(group) > 1 and group[-1].end - group[0].start > max_group_sec
        ]
        if not oversized:
            break
        group_index, _ = max(oversized, key=lambda item: item[1])
        group = groups[group_index]
        split_after = max(
            range(len(group) - 1),
            key=lambda index: group[index + 1].start - group[index].end,
        )
        left = group[: split_after + 1]
        right = group[split_after + 1 :]
        groups[group_index : group_index + 1] = [left, right]

    group_chunks: list[AudioChunk] = []
    for group_index, group in enumerate(groups):
        for chunk in group:
            chunk.vad_group_index = group_index
        first = group[0]
        last = group[-1]
        group_chunks.append(
            AudioChunk(
                index=group_index,
                start=first.start,
                end=last.end,
                samples=[],
                vad_activation=sum(chunk.vad_activation for chunk in group) / len(group),
                vad_peak=max(chunk.vad_peak for chunk in group),
                vad_group_index=group_index,
            )
        )
    return group_chunks


def _chunks_from_spans(
    spans: list[tuple[int, int]],
    samples: Any,
    sample_rate: int,
    speech_probs: list[float],
    window_size_samples: int,
    temp_dir: Path | None,
    keep_temp: bool,
) -> list[AudioChunk]:
    chunks: list[AudioChunk] = []
    for index, (start_sample, end_sample) in enumerate(spans):
        chunk_samples = samples[start_sample:end_sample]
        if len(chunk_samples) == 0:
            continue
        activation, peak = _span_activation(speech_probs, window_size_samples, start_sample, end_sample)
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
                vad_activation=activation,
                vad_peak=peak,
                vad_group_index=index,
            )
        )
    return chunks


def select_high_activation_chunks(
    chunks: list[AudioChunk],
    target_duration_ratio: float = 0.20,
    min_chunks: int = 1,
) -> list[AudioChunk]:
    """Return high-activation chunks until the selected active voice duration target is reached."""
    if not chunks:
        return []
    bounded_ratio = min(1.0, max(0.0, target_duration_ratio))
    total_duration = sum(max(0.0, chunk.end - chunk.start) for chunk in chunks)
    target_duration = total_duration * bounded_ratio
    if target_duration <= 0 and min_chunks <= 0:
        return []
    ranked = sorted(
        chunks,
        key=lambda chunk: (
            chunk.vad_activation,
            chunk.vad_peak,
            max(0.0, chunk.end - chunk.start),
        ),
        reverse=True,
    )
    selected: list[AudioChunk] = []
    selected_duration = 0.0
    min_selected = min(len(chunks), max(0, min_chunks))
    for chunk in ranked:
        if selected_duration >= target_duration and len(selected) >= min_selected:
            break
        selected.append(chunk)
        selected_duration += max(0.0, chunk.end - chunk.start)
    return sorted(selected, key=lambda chunk: (chunk.start, chunk.end))


def split_chunk_with_tighter_vad(
    chunk: AudioChunk,
    sample_rate: int,
    temp_dir: Path | None = None,
    keep_temp: bool = False,
    min_piece_sec: float = 0.25,
    session: VadSession | None = None,
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
    try:
        probabilities, window_size = (session or VadSession()).probabilities(chunk.samples, sample_rate)
    except Exception:
        probabilities = []
        window_size = 512 if sample_rate == 16000 else 256
    for max_chunk_sec, min_silence_ms, speech_pad_ms in attempts:
        speech = _speech_timestamps_from_probabilities(
            probabilities,
            window_size,
            sample_rate,
            len(chunk.samples),
            max_chunk_sec,
            min_piece_sec,
            min_silence_ms,
            speech_pad_ms,
        )
        spans = _merge_speech_timestamps(
            speech, sample_rate, max_chunk_sec, min_silence_ms, speech_pad_ms, len(chunk.samples)
        )
        local = _chunks_from_spans(
            spans, chunk.samples, sample_rate, probabilities, window_size, None, False
        )
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
