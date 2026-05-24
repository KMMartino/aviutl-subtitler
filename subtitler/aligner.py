"""Forced alignment helpers."""

from __future__ import annotations

import contextlib
import warnings
from pathlib import Path

from .audio import write_wav_segment
from .errors import AlignmentError
from .models import AlignedChunk, AlignedToken, TranscriptChunk


class AlignmentTooLongError(AlignmentError):
    """The transcript is too dense or long for the CTC emission length."""


def proportional_alignment(item: TranscriptChunk, language: str) -> AlignedChunk:
    text = item.text
    chars = list(text) if language == "ja" else text.split()
    chars = [c for c in chars if c.strip()]
    if not chars:
        return AlignedChunk(chunk=item.chunk, text=text, tokens=[], fallback=True)
    duration = max(item.chunk.end - item.chunk.start, 0.001)
    step = duration / len(chars)
    kind = "char" if language == "ja" else "word"
    tokens = [
        AlignedToken(
            text=part,
            start=item.chunk.start + i * step,
            end=item.chunk.start + (i + 1) * step,
            kind=kind,
        )
        for i, part in enumerate(chars)
    ]
    return AlignedChunk(chunk=item.chunk, text=text, tokens=tokens, fallback=True)


class ForcedAligner:
    def __init__(
        self,
        model_name: str,
        language: str,
        device: str,
        split_size: str,
        star_frequency: str,
        temp_dir: Path,
        sample_rate: int,
        emission_batch_size: int = 4,
        torch_threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.language = language
        self.device = device
        self.split_size = split_size
        self.star_frequency = star_frequency
        self.temp_dir = temp_dir
        self.sample_rate = sample_rate
        self.emission_batch_size = emission_batch_size
        self.torch_threads = torch_threads
        self.alignment_model = None
        self.alignment_tokenizer = None
        self._load_error: Exception | None = None
        self._load_model()

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _load_model(self) -> None:
        try:
            from ctc_forced_aligner import load_alignment_model
            import torch
            from transformers.utils import logging as transformers_logging

            self.device = self._resolve_device()
            transformers_logging.disable_progress_bar()
            warnings.filterwarnings(
                "ignore",
                message="The given buffer is not writable.*",
                category=UserWarning,
            )
            if self.device == "cpu" and self.torch_threads:
                torch.set_num_threads(max(1, self.torch_threads))
                with contextlib.suppress(RuntimeError):
                    torch.set_num_interop_threads(1)
            self.alignment_model, self.alignment_tokenizer = load_alignment_model(
                self.device,
                model_path=self.model_name,
            )
        except Exception as exc:
            self._load_error = exc

    def align(self, item: TranscriptChunk) -> AlignedChunk:
        if not item.text.strip():
            return AlignedChunk(chunk=item.chunk, text=item.text, tokens=[], fallback=False)
        if self._load_error is not None:
            print(
                "Warning: alignment model could not be loaded; using proportional timing. "
                f"{self._load_error}"
            )
            return proportional_alignment(item, self.language)
        if self.alignment_model is None or self.alignment_tokenizer is None:
            raise AlignmentError("Alignment model was not initialized")

        wav_path = item.chunk.wav_path or self.temp_dir / f"align_{item.chunk.index:05d}.wav"
        if item.chunk.wav_path is None:
            write_wav_segment(item.chunk.samples, self.sample_rate, wav_path)

        try:
            # ctc-forced-aligner has had CLI/API differences across releases.
            # Prefer its Python API when available; fall back to proportional
            # timing only for non-structural aligner failures.
            from ctc_forced_aligner import (
                generate_emissions,
                get_alignments,
                get_spans,
                load_audio,
                postprocess_results,
                preprocess_text,
            )

            audio_waveform = load_audio(
                str(wav_path),
                self.alignment_model.dtype,
                self.alignment_model.device,
            )
            emissions, stride = generate_emissions(
                self.alignment_model,
                audio_waveform,
                batch_size=self.emission_batch_size,
            )
            tokens_starred, text_starred = preprocess_text(
                item.text,
                romanize=True,
                language=self.language,
                split_size=self.split_size,
                star_frequency=self.star_frequency,
            )
            segments, scores, blank_token = get_alignments(
                emissions,
                tokens_starred,
                self.alignment_tokenizer,
            )
            spans = get_spans(tokens_starred, segments, blank_token)
            results = postprocess_results(text_starred, spans, stride, scores)
            tokens = []
            for result in results:
                token_text = str(result.get("text", "")).strip()
                if not token_text:
                    continue
                start = item.chunk.start + float(result.get("start", 0.0))
                end = item.chunk.start + float(result.get("end", 0.0))
                tokens.append(
                    AlignedToken(
                        text=token_text,
                        start=max(item.chunk.start, min(start, item.chunk.end)),
                        end=max(item.chunk.start, min(end, item.chunk.end)),
                        kind="char" if self.split_size == "char" else "word",
                    )
                )
            if tokens:
                return AlignedChunk(chunk=item.chunk, text=item.text, tokens=tokens, fallback=False)
        except Exception as exc:
            if _is_ctc_too_long_error(exc):
                raise AlignmentTooLongError(str(exc)) from exc
            print(f"Warning: alignment failed for chunk {item.chunk.index}; using proportional timing. {exc}")

        return proportional_alignment(item, self.language)


def _is_ctc_too_long_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "targets length" in message and "too long" in message and "ctc" in message
