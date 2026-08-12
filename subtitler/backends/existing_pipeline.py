"""Existing Silero VAD -> ASR -> CTC alignment backend."""

from __future__ import annotations

import math
import os
import re
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from subtitler.aligner import ctc_language_code, is_japanese_language
from subtitler.alignment_pool import AlignmentConfig, AlignmentPool
from subtitler.api_costs import estimate_run_cost
from subtitler.api_usage import ApiUsageLedger
from subtitler.audio import write_wav_segment
from subtitler.errors import SubtitlerError
from subtitler.external_transcribers import (
    FallbackTranscriber,
    GeminiTranscriber,
    GPTTranscribeAdapter,
    MalformedTranscriptionResponse,
    OpenAITranscriber,
)
from subtitler.models import AlignedChunk, AlignedToken, AudioChunk, TranscriptChunk
from subtitler.profiling import PipelineProfiler, now
from subtitler.transcriber import ServerGemmaTranscriber
from subtitler.transcription_backend import (
    BackendCapability,
    BackendDiagnostic,
    BackendStatus,
    BackendTranscriptResult,
    SpeechRegion,
    RawVadSpeechInterval,
    TranscriptSegment,
    TranscriptToken,
    TranscriptionRequest,
)
from subtitler.vad import (
    VadSession,
    assign_vad_groups_by_largest_gaps,
    segment_speech_with_groups,
    select_high_activation_chunks,
    split_chunk_with_tighter_vad,
)
from subtitler.silence_cut import build_cut_candidates


FAILED_TRANSCRIPTION_TEXT = "transcription failed"


ALIGNER_CPU_MEMORY_BUDGET_BYTES = 2 * 1024**3


def available_memory_bytes() -> int | None:
    """Best-effort available-memory reading without adding a runtime dependency."""
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("avail_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.avail_phys)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        sysconf = getattr(os, "sysconf")
        page_size = sysconf("SC_PAGE_SIZE")
        available_pages = sysconf("SC_AVPHYS_PAGES")
        return int(page_size * available_pages)
    except (AttributeError, OSError, ValueError):
        return None


def alignment_uses_gpu(device: str, cuda_available: bool | None = None) -> bool:
    normalized = device.strip().lower()
    if normalized != "auto":
        return normalized != "cpu"
    if cuda_available is not None:
        return cuda_available
    try:
        import torch

        return bool(torch.cuda.is_available())
    except (ImportError, RuntimeError):
        return False


def default_align_workers(
    device: str = "cpu",
    available_bytes: int | None = None,
    cuda_available: bool | None = None,
) -> int:
    """Choose a conservative model replica count; explicit config still overrides it."""
    if alignment_uses_gpu(device, cuda_available):
        return 1
    core_limit = max(1, (os.cpu_count() or 4) // 4)
    memory = available_memory_bytes() if available_bytes is None else available_bytes
    if memory is None:
        return core_limit
    memory_limit = max(1, memory // ALIGNER_CPU_MEMORY_BUDGET_BYTES)
    return max(1, min(core_limit, memory_limit))


@dataclass(frozen=True)
class CleanupGroupPolicy:
    min_sec: float = 60.0
    duration_divisor: float = 2.0
    max_sec: float = 600.0

    def max_group_sec(self, media_duration_sec: float) -> float:
        scaled = max(0.0, media_duration_sec) / self.duration_divisor
        return max(self.min_sec, min(scaled, self.max_sec))


def cleanup_group_policy(config: dict[str, Any]) -> CleanupGroupPolicy:
    cleanup = config.get("cleanup", {})
    return CleanupGroupPolicy(
        min_sec=float(cleanup.get("group_min_sec") or 60.0),
        duration_divisor=float(cleanup.get("group_duration_divisor") or 2.0),
        max_sec=float(cleanup.get("group_max_sec") or 600.0),
    )


def _cleanup_group_max_sec(media_duration_sec: float, policy: CleanupGroupPolicy | None = None) -> float:
    return (policy or CleanupGroupPolicy()).max_group_sec(media_duration_sec)


@dataclass
class SpeechSelection:
    all_chunks: list[AudioChunk]
    selected_chunks: list[AudioChunk]
    speech_regions: list[SpeechRegion]
    selected_speech_seconds: float
    total_speech_seconds: float


@dataclass
class HostedAttemptOutcome:
    chunk: AudioChunk
    status: Literal["success", "untranscribable", "quality_failure", "transport_failure"]
    transcript: TranscriptChunk | None = None
    error: Exception | None = None
    provider: str = ""
    model: str = ""


@dataclass
class HostedSplitRecovery:
    text: str
    unrecovered: list[tuple[AudioChunk, Exception]]


class ExistingPipelineBackend:
    name = "existing-pipeline"

    def __init__(self, config: dict[str, Any], api_usage: ApiUsageLedger, profiler: PipelineProfiler) -> None:
        self.config = config
        self.api_usage = api_usage
        self.profiler = profiler
        self.capabilities = BackendCapability(
            provides_vad=True,
            provides_segment_timestamps=True,
            provides_token_timestamps=True,
            provides_word_timestamps=True,
            provides_char_timestamps=True,
            requires_external_alignment=True,
            supports_long_stream_selection=True,
            supports_glossary=True,
        )

    def transcribe(self, request: TranscriptionRequest) -> BackendTranscriptResult:
        backend_cfg = self.config["backend"]
        vad_cfg = self.config["vad"]
        workflow_cfg = self.config["workflow"]
        alignment_cfg = self.config["alignment"]

        print("Running Silero VAD...")
        cleanup_group_max_sec = _cleanup_group_max_sec(request.duration_sec, cleanup_group_policy(self.config))
        vad_session = VadSession()
        configured_vad_max_sec = float(vad_cfg["max_chunk_sec"])
        transcription_vad_max_sec = (
            min(configured_vad_max_sec, 30.0)
            if backend_cfg["transcriber"] == "local-gemma"
            else configured_vad_max_sec
        )
        if transcription_vad_max_sec < configured_vad_max_sec:
            print(
                f"Local transcription VAD maximum capped at {transcription_vad_max_sec:.1f}s "
                f"(configured {configured_vad_max_sec:.1f}s).",
                flush=True,
            )
        vad_segmentation = segment_speech_with_groups(
            samples=request.metadata["samples"],
            sample_rate=request.sample_rate,
            max_chunk_sec=transcription_vad_max_sec,
            min_speech_sec=float(vad_cfg["min_speech_sec"]),
            min_silence_ms=int(vad_cfg["min_silence_ms"]),
            speech_pad_ms=int(vad_cfg["speech_pad_ms"]),
            cleanup_group_max_sec=cleanup_group_max_sec,
            temp_dir=request.temp_dir,
            keep_temp=True,
            progress_callback=request.metadata.get("stage_progress_reporter"),
            session=vad_session,
        )
        if len(vad_segmentation) == 2:  # Backward-compatible test/plugin seam.
            chunks, vad_groups = vad_segmentation
            raw_vad_intervals = [(chunk.start, chunk.end) for chunk in chunks]
        else:
            chunks, vad_groups, raw_vad_intervals = vad_segmentation
        print(
            f"VAD chunks: {len(chunks)} fine, {len(vad_groups)} cleanup group(s) "
            f"(cleanup_group_max_sec={cleanup_group_max_sec:.2f})",
            flush=True,
        )
        selection = build_speech_selection(workflow_cfg, chunks, request.duration_sec)
        transcription_chunks = selection.selected_chunks
        if uses_larger_hosted_transcription_segments(self.config):
            transcription_chunks = build_hosted_transcription_chunks(
                all_chunks=chunks,
                selected_chunks=selection.selected_chunks,
                samples=request.metadata["samples"],
                sample_rate=request.sample_rate,
                max_group_sec=cleanup_group_max_sec,
                temp_dir=request.temp_dir,
            )
            print(
                f"Hosted transcription segments: {len(transcription_chunks)} larger VAD group(s) "
                f"from {len(selection.selected_chunks)} selected fine chunk(s).",
                flush=True,
            )
        normalized_raw_vad = [RawVadSpeechInterval(start, end) for start, end in raw_vad_intervals]
        control_event = request.metadata.get("control_event")
        cut_mode = self.config["additional_settings"].get("cut_silence_mode", "off")
        if callable(control_event) and cut_mode == "review":
            control_event(
                "silence-candidates",
                workflow=request.workflow,
                candidates=[candidate.to_frontend() for candidate in build_cut_candidates(normalized_raw_vad)],
            )

        if request.profile_enabled and request.sidecar_base is not None:
            write_vad_selection(request.sidecar_base.with_suffix(".vad_selection.csv"), chunks, selection.selected_chunks)
            write_vad_selection(request.sidecar_base.with_suffix(".vad_groups.csv"), vad_groups, vad_groups)

        transcribed_audio_seconds = sum(
            max(0.0, chunk.end - chunk.start) for chunk in transcription_chunks
        )
        estimated_api_cost = estimate_backend_run_cost(self.config, transcribed_audio_seconds)
        print(
            "Estimated hosted API cost: "
            f"${estimated_api_cost:.4f} "
            f"(transcribed_audio={transcribed_audio_seconds / 60.0:.2f} min, "
            f"vad_speech={selection.total_speech_seconds / 60.0:.2f} min)",
            flush=True,
        )
        hosted_run = backend_cfg["transcriber"] in {"gemini", "openai"} or self.config["cleanup"]["backend"] in {"gemini", "openai"}
        _, _, estimate_cost_only = _validated_cost_guard_settings(self.config, estimated_api_cost)
        if hosted_run and estimate_cost_only:
            return BackendTranscriptResult(
                backend_name=self.name,
                model_name=transcription_model(self.config),
                status="partial",
                language=request.language,
                duration_sec=request.duration_sec,
                segments=[],
                speech_regions=selection.speech_regions,
                raw_vad_speech_intervals=normalized_raw_vad,
                diagnostics=[
                    BackendDiagnostic(
                        level="info",
                        message=f"Estimated hosted API cost: ${estimated_api_cost:.4f}",
                        code="cost_estimate_only",
                    )
                ],
                capabilities=self.capabilities,
                metadata=_backend_metadata(
                    self.config,
                    selection,
                    estimated_api_cost,
                    transcription_chunks,
                ),
            )
        enforce_cost_guard(self.config, estimated_api_cost)

        for chunk in transcription_chunks:
            self.profiler.start_chunk(chunk.index, chunk.start, chunk.end)

        transcriber = self._build_transcriber(request, vad_session)
        try:
            split_size = "char" if is_japanese_language(request.language) else "word"
            ctc_language = ctc_language_code(request.language)
            print(
                "Alignment: "
                f"model={alignment_cfg['model']}, language={request.language}, "
                f"ctc_language={ctc_language}, split_size={split_size}, star_frequency=edges",
                flush=True,
            )
            align_workers = int(
                alignment_cfg["workers"] or default_align_workers(str(alignment_cfg["device"]))
            )
            torch_threads = alignment_cfg["torch_threads"]
            if torch_threads is None:
                cpu_count = os.cpu_count() or 4
                torch_threads = max(1, cpu_count // max(1, align_workers))
            config = AlignmentConfig(
                model_name=alignment_cfg["model"],
                language=request.language,
                device=alignment_cfg["device"],
                split_size=split_size,
                temp_dir=request.temp_dir,
                sample_rate=request.sample_rate,
                emission_batch_size=int(alignment_cfg["emission_batch_size"]),
                torch_threads=int(torch_threads),
                max_split_depth=max(0, int(alignment_cfg["max_split_depth"])),
                vad_session=vad_session,
            )
            aligned, failed_transcripts = transcribe_and_align(
                chunks=transcription_chunks,
                transcriber=transcriber,
                alignment_config=config,
                profiler=self.profiler,
                audio_prep_workers=max(1, int(backend_cfg["audio_prep_workers"])),
                align_workers=max(1, align_workers),
                transcription_workers=max(1, transcription_workers(self.config)),
                hosted_recovery_depth=max(0, int(backend_cfg["transcription_max_split_depth"])),
                hosted_recovery_temp_dir=request.temp_dir,
                hosted_recovery_vad_session=vad_session,
                hosted_recovery_min_silence_ms=int(vad_cfg["min_silence_ms"]),
                hosted_recovery_speech_pad_ms=int(vad_cfg["speech_pad_ms"]),
            )
        finally:
            close = getattr(transcriber, "close", None)
            if close is not None:
                close()

        segments = aligned_chunks_to_segments(aligned, request.language)
        status = transcription_result_status(
            selected_chunk_count=len(transcription_chunks),
            usable_segment_count=sum(bool(segment.text.strip()) for segment in segments),
            failed_chunk_count=len(failed_transcripts),
        )
        return BackendTranscriptResult(
            backend_name=self.name,
            model_name=transcription_model(self.config),
            status=status,
            language=request.language,
            duration_sec=request.duration_sec,
            segments=segments,
            speech_regions=selection.speech_regions,
            raw_vad_speech_intervals=normalized_raw_vad,
            diagnostics=[
                BackendDiagnostic(
                    level="error" if status == "failed" else "warning",
                    message=f"Transcription failed for chunk {item.chunk.index}",
                    region_index=item.chunk.index,
                    code="transcription_failed",
                    metadata={"error": item.error} if item.error else {},
                )
                for item in failed_transcripts
            ],
            capabilities=self.capabilities,
            metadata=_backend_metadata(
                self.config,
                selection,
                estimated_api_cost,
                transcription_chunks,
            ),
        )

    def _build_transcriber(self, request: TranscriptionRequest, vad_session: VadSession | None = None):
        backend_cfg = self.config["backend"]
        name = backend_cfg["transcriber"]
        model = transcription_model(self.config)
        allow_sparse_transcript = self.config["workflow"]["mode"] == "long-stream"
        if name == "local-gemma":
            if not model:
                raise SubtitlerError("Local workflow requires backend.model")
            return ServerGemmaTranscriber(
                model_path=Path(model),
                mmproj=Path(backend_cfg["mmproj"]) if backend_cfg.get("mmproj") else None,
                n_gpu_layers=int(backend_cfg["n_gpu_layers"]),
                ctx_size=int(backend_cfg["ctx_size"]),
                temp_dir=request.temp_dir,
                server_path=Path(backend_cfg["llama_server"]) if backend_cfg.get("llama_server") else None,
                host="127.0.0.1",
                port=int(backend_cfg["server_port"]),
                glossary=request.glossary,
                max_transcription_split_depth=max(0, int(backend_cfg["transcription_max_split_depth"])),
                spec_draft_model=Path(backend_cfg["spec_draft_model"]) if backend_cfg.get("spec_draft_model") else None,
                spec_draft_n_max=int(backend_cfg["spec_draft_n_max"]),
                log_path=(
                    request.sidecar_base.with_suffix(".transcription_llama.log")
                    if request.sidecar_base is not None
                    else request.temp_dir / "transcription_llama.log"
                ),
                vad_session=vad_session,
            )
        if not model:
            raise SubtitlerError("Hosted workflow requires backend.transcription_model")
        if name == "gemini":
            return FallbackTranscriber(
                GeminiTranscriber(
                    model=model,
                    temp_dir=request.temp_dir,
                    usage=self.api_usage,
                    glossary=request.glossary,
                    allow_sparse_transcript=allow_sparse_transcript,
                ),
                self._build_fallback_transcriber(request, allow_sparse_transcript=allow_sparse_transcript),
            )
        if name == "openai":
            transcriber_type = GPTTranscribeAdapter if model == "gpt-transcribe" else OpenAITranscriber
            return FallbackTranscriber(
                transcriber_type(
                    model=model,
                    temp_dir=request.temp_dir,
                    usage=self.api_usage,
                    glossary=request.glossary,
                    language=request.language,
                    allow_sparse_transcript=allow_sparse_transcript,
                ),
                self._build_fallback_transcriber(request, allow_sparse_transcript=allow_sparse_transcript),
            )
        raise SubtitlerError(f"Unknown existing-pipeline transcriber: {name}")

    def _build_fallback_transcriber(
        self,
        request: TranscriptionRequest,
        *,
        allow_sparse_transcript: bool = False,
    ):
        backend_cfg = self.config["backend"]
        name = str(backend_cfg.get("fallback_transcriber") or "").strip()
        model = str(backend_cfg.get("fallback_transcription_model") or "").strip()
        if not name or not model:
            return None
        if name == backend_cfg["transcriber"] and model == transcription_model(self.config):
            return None
        if name == "gemini":
            return GeminiTranscriber(
                model=model,
                temp_dir=request.temp_dir,
                usage=self.api_usage,
                glossary=request.glossary,
                timeout_scale=2.0,
                allow_sparse_transcript=allow_sparse_transcript,
            )
        if name == "openai":
            transcriber_type = GPTTranscribeAdapter if model == "gpt-transcribe" else OpenAITranscriber
            return transcriber_type(
                model=model,
                temp_dir=request.temp_dir,
                usage=self.api_usage,
                glossary=request.glossary,
                language=request.language,
                timeout_scale=2.0,
                allow_sparse_transcript=allow_sparse_transcript,
            )
        raise SubtitlerError(f"Unknown hosted fallback transcriber: {name}")


def transcription_model(config: dict[str, Any]) -> str:
    backend = config["backend"]
    if backend["transcriber"] == "local-gemma":
        return backend.get("model") or backend.get("transcription_model") or ""
    return backend.get("transcription_model") or backend.get("model") or ""


def cleanup_model(config: dict[str, Any]) -> str:
    cleanup = config["cleanup"]
    return cleanup.get("api_model") if cleanup["backend"] in {"gemini", "openai"} else cleanup.get("model", "")


def estimate_backend_run_cost(config: dict[str, Any], speech_seconds: float) -> float:
    backend = config["backend"]
    cleanup = config["cleanup"]
    return estimate_run_cost(
        transcriber_backend=backend["transcriber"],
        transcription_model=transcription_model(config),
        cleanup_backend=cleanup["backend"],
        cleanup_model=cleanup_model(config),
        speech_seconds=speech_seconds,
    )


def is_hosted_run(config: dict[str, Any]) -> bool:
    return config["backend"]["transcriber"] in {"gemini", "openai"} or config["cleanup"]["backend"] in {"gemini", "openai"}


def enforce_cost_guard(config: dict[str, Any], estimated_api_cost: float) -> None:
    try:
        hosted_run = is_hosted_run(config)
    except (KeyError, TypeError) as exc:
        raise SubtitlerError("Refusing hosted API use: workflow backend configuration is invalid") from exc
    if not hosted_run:
        return
    max_cost, allow_api_spend, _ = _validated_cost_guard_settings(config, estimated_api_cost)
    if estimated_api_cost > max_cost and not allow_api_spend:
        raise SubtitlerError(
            "estimated hosted API cost "
            f"${estimated_api_cost:.4f} exceeds configured limit ${max_cost:.2f}. "
            "Set cost.allow_api_spend to true in the workflow config to proceed."
        )


def _validated_cost_guard_settings(
    config: dict[str, Any], estimated_api_cost: float
) -> tuple[float, bool, bool]:
    cost_cfg = config.get("cost")
    if not isinstance(cost_cfg, dict):
        raise SubtitlerError("Refusing hosted API use: cost must be a config object")
    max_cost = cost_cfg.get("max_estimated_api_cost_usd")
    allow_api_spend = cost_cfg.get("allow_api_spend")
    estimate_cost_only = cost_cfg.get("estimate_cost_only")
    if (
        isinstance(max_cost, bool)
        or not isinstance(max_cost, (int, float))
        or not math.isfinite(max_cost)
        or max_cost < 0
    ):
        raise SubtitlerError(
            "Refusing hosted API use: cost.max_estimated_api_cost_usd must be a finite non-negative number"
        )
    if not isinstance(allow_api_spend, bool):
        raise SubtitlerError("Refusing hosted API use: cost.allow_api_spend must be a boolean")
    if not isinstance(estimate_cost_only, bool):
        raise SubtitlerError("Refusing hosted API use: cost.estimate_cost_only must be a boolean")
    if (
        isinstance(estimated_api_cost, bool)
        or not isinstance(estimated_api_cost, (int, float))
        or not math.isfinite(estimated_api_cost)
        or estimated_api_cost < 0
    ):
        raise SubtitlerError("Refusing hosted API use: estimated API cost must be a finite non-negative number")
    return float(max_cost), allow_api_spend, estimate_cost_only


def _backend_metadata(
    config: dict[str, Any],
    selection: SpeechSelection,
    estimated_api_cost: float,
    transcription_chunks: list[AudioChunk] | None = None,
) -> dict[str, Any]:
    chunks = selection.selected_chunks if transcription_chunks is None else transcription_chunks
    return {
        "transcriber": config["backend"]["transcriber"],
        "selected_speech_seconds": selection.selected_speech_seconds,
        "total_speech_seconds": selection.total_speech_seconds,
        "transcribed_audio_seconds": sum(max(0.0, chunk.end - chunk.start) for chunk in chunks),
        "transcription_segment_count": len(chunks),
        "estimated_api_cost_usd": estimated_api_cost,
    }


def transcription_result_status(
    *, selected_chunk_count: int, usable_segment_count: int, failed_chunk_count: int
) -> BackendStatus:
    """Summarize the outcome of selected-speech transcription and alignment.

    A run with no selected chunks is a valid empty result. Once speech chunks are
    selected, producing no usable aligned segment is a failed result. Otherwise,
    explicit transcription failures make the usable result partial.
    """
    if selected_chunk_count > 0 and usable_segment_count == 0:
        return "failed"
    if failed_chunk_count > 0:
        return "partial"
    return "ok"


def transcription_workers(config: dict[str, Any]) -> int:
    backend = config["backend"]
    explicit = backend.get("transcription_workers")
    hosted = backend["transcriber"] != "local-gemma"
    if explicit is not None:
        return max(1, int(explicit))
    return 6 if hosted else 1


def uses_larger_hosted_transcription_segments(config: dict[str, Any]) -> bool:
    return (
        config["backend"]["transcriber"] == "openai"
        and transcription_model(config) == "gpt-transcribe"
    )


def long_stream_default_duration_ratio(media_duration_sec: float) -> float:
    duration_hours = max(0.0, media_duration_sec) / 3600.0
    t = min(1.0, duration_hours / 5.0)
    smooth = t * t * (3.0 - 2.0 * t)
    return 0.15 + (0.07 - 0.15) * smooth


def build_speech_selection(workflow_cfg: dict[str, Any], chunks: list[AudioChunk], media_duration_sec: float) -> SpeechSelection:
    selected_chunks = select_transcription_chunks(workflow_cfg, chunks, media_duration_sec)
    selected_ids = {chunk.index for chunk in selected_chunks}
    speech_regions = [
        SpeechRegion(
            index=chunk.index,
            start=chunk.start,
            end=chunk.end,
            selected_for_transcription=chunk.index in selected_ids,
            activation=chunk.vad_activation,
            peak=chunk.vad_peak,
            source="silero",
            metadata={"vad_group_index": chunk.vad_group_index},
        )
        for chunk in chunks
    ]
    return SpeechSelection(
        all_chunks=chunks,
        selected_chunks=selected_chunks,
        speech_regions=speech_regions,
        selected_speech_seconds=sum(max(0.0, chunk.end - chunk.start) for chunk in selected_chunks),
        total_speech_seconds=sum(max(0.0, chunk.end - chunk.start) for chunk in chunks),
    )


def build_hosted_transcription_chunks(
    *,
    all_chunks: list[AudioChunk],
    selected_chunks: list[AudioChunk],
    samples: Any,
    sample_rate: int,
    max_group_sec: float,
    temp_dir: Path,
) -> list[AudioChunk]:
    """Pack consecutive selected VAD chunks into continuous hosted-audio groups."""
    if not selected_chunks:
        return []
    ordered_all = sorted(all_chunks, key=lambda item: (item.start, item.end, item.index))
    positions = {chunk.index: position for position, chunk in enumerate(ordered_all)}
    ordered_selected = sorted(
        selected_chunks,
        key=lambda item: (positions.get(item.index, len(positions)), item.start, item.end),
    )
    consecutive_runs: list[list[AudioChunk]] = []
    for chunk in ordered_selected:
        position = positions.get(chunk.index)
        if (
            not consecutive_runs
            or position is None
            or positions.get(consecutive_runs[-1][-1].index) != position - 1
        ):
            consecutive_runs.append([chunk])
        else:
            consecutive_runs[-1].append(chunk)

    metadata_groups: list[AudioChunk] = []
    for run in consecutive_runs:
        copies = [
            AudioChunk(
                index=chunk.index,
                start=chunk.start,
                end=chunk.end,
                samples=[],
                vad_activation=chunk.vad_activation,
                vad_peak=chunk.vad_peak,
                vad_group_index=chunk.vad_group_index,
            )
            for chunk in run
        ]
        metadata_groups.extend(
            assign_vad_groups_by_largest_gaps(copies, max_group_sec=max_group_sec)
        )

    result: list[AudioChunk] = []
    total_samples = len(samples)
    for index, group in enumerate(sorted(metadata_groups, key=lambda item: (item.start, item.end))):
        start_sample = max(0, min(total_samples, round(group.start * sample_rate)))
        end_sample = max(start_sample, min(total_samples, round(group.end * sample_rate)))
        group_samples = samples[start_sample:end_sample]
        wav_path = temp_dir / f"hosted_transcription_group_{index:05d}.wav"
        write_wav_segment(group_samples, sample_rate, wav_path)
        result.append(
            AudioChunk(
                index=index,
                start=start_sample / sample_rate,
                end=end_sample / sample_rate,
                samples=group_samples,
                wav_path=wav_path,
                vad_activation=group.vad_activation,
                vad_peak=group.vad_peak,
                vad_group_index=index,
            )
        )
    return result


def select_transcription_chunks(workflow_cfg: dict[str, Any], chunks: list[AudioChunk], media_duration_sec: float) -> list[AudioChunk]:
    if (
        workflow_cfg["mode"] != "long-stream"
        or workflow_cfg.get("transcription_scope", "full") == "full"
    ):
        if workflow_cfg["mode"] == "long-stream" and chunks:
            total_speech_minutes = sum(max(0.0, chunk.end - chunk.start) for chunk in chunks) / 60.0
            print(
                "Long-stream mode: full detected speech selected "
                f"({len(chunks)} VAD chunks, {total_speech_minutes:.2f} active voice min).",
                flush=True,
            )
        return chunks
    ratio = workflow_cfg.get("long_stream_selection_ratio")
    duration_ratio = long_stream_default_duration_ratio(media_duration_sec) if ratio is None else max(0.0, min(1.0, float(ratio)))
    selected = select_high_activation_chunks(
        chunks,
        target_duration_ratio=duration_ratio,
        min_chunks=max(0, int(workflow_cfg["long_stream_min_chunks"])),
    )
    if chunks:
        threshold = min((chunk.vad_activation for chunk in selected), default=0.0)
        selected_speech_minutes = sum(max(0.0, chunk.end - chunk.start) for chunk in selected) / 60.0
        total_speech_minutes = sum(max(0.0, chunk.end - chunk.start) for chunk in chunks) / 60.0
        print(
            "Long-stream mode: "
            f"selected {len(selected)}/{len(chunks)} VAD chunks "
            f"({selected_speech_minutes:.2f}/{total_speech_minutes:.2f} active voice min, "
            f"target={duration_ratio * 100.0:.1f}%) by VAD activation >= {threshold:.4f}.",
            flush=True,
        )
    return selected


def write_vad_selection(path: Path, chunks: list[AudioChunk], selected_chunks: list[AudioChunk]) -> None:
    selected_ids = {chunk.index for chunk in selected_chunks}
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["chunk_index,vad_group_index,start,end,duration_sec,vad_activation,vad_peak,selected_for_transcription"]
    for chunk in sorted(chunks, key=lambda item: (item.start, item.end)):
        duration = max(0.0, chunk.end - chunk.start)
        lines.append(
            f"{chunk.index},{chunk.vad_group_index if chunk.vad_group_index is not None else ''},"
            f"{chunk.start:.6f},{chunk.end:.6f},{duration:.6f},"
            f"{chunk.vad_activation:.6f},{chunk.vad_peak:.6f},{str(chunk.index in selected_ids).lower()}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def aligned_chunks_to_segments(chunks: list[AlignedChunk], language: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for item in sorted(chunks, key=lambda chunk: (chunk.chunk.start, chunk.chunk.end, chunk.chunk.index)):
        timing_kind = "char" if is_japanese_language(language) else "word"
        segments.append(
            TranscriptSegment(
                index=item.chunk.index,
                text=item.text,
                start=item.chunk.start,
                end=item.chunk.end,
                tokens=[
                    TranscriptToken(text=token.text, start=token.start, end=token.end, kind=token.kind, source="ctc")
                    for token in item.tokens
                ],
                language=language,
                timing_kind=timing_kind if item.tokens else "segment",
                fallback_timing=item.fallback,
                source="existing-pipeline",
                metadata={"vad_group_index": item.chunk.vad_group_index},
            )
        )
    return segments


def transcribe_and_align(
    chunks,
    transcriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    audio_prep_workers: int,
    align_workers: int,
    transcription_workers: int = 1,
    hosted_recovery_depth: int = 0,
    hosted_recovery_temp_dir: Path | None = None,
    hosted_recovery_vad_session: VadSession | None = None,
    hosted_recovery_min_silence_ms: int = 400,
    hosted_recovery_speech_pad_ms: int = 200,
):
    if not chunks:
        return [], []
    if isinstance(transcriber, FallbackTranscriber):
        return transcribe_and_align_hosted(
            chunks,
            transcriber,
            alignment_config,
            profiler,
            transcription_workers,
            align_workers,
            recovery_max_split_depth=hosted_recovery_depth,
            recovery_temp_dir=hosted_recovery_temp_dir,
            recovery_vad_session=hosted_recovery_vad_session,
            recovery_min_silence_ms=hosted_recovery_min_silence_ms,
            recovery_speech_pad_ms=hosted_recovery_speech_pad_ms,
        )
    if hasattr(transcriber, "prepare_payload") and hasattr(transcriber, "transcribe_payload"):
        return transcribe_and_align_server(chunks, transcriber, alignment_config, profiler, audio_prep_workers, align_workers)
    if transcription_workers > 1:
        return transcribe_and_align_parallel(chunks, transcriber, alignment_config, profiler, transcription_workers, align_workers)

    pool = AlignmentPool(capped_align_workers(align_workers, len(chunks)), alignment_config, profiler)
    failed: list[TranscriptChunk] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"Transcribing chunk {i}/{len(chunks)} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
        transcript = transcribe_one(transcriber, chunk, profiler)
        if is_failed_transcript(transcript):
            failed.append(transcript)
            continue
        if not transcript.text:
            print(f"Warning: empty transcript for chunk {chunk.index}")
            continue
        pool.submit(transcript)
    print("Waiting for alignment workers...", flush=True)
    aligned = pool.close_and_collect()
    print_transcription_failure_summary(failed)
    return aligned, failed


def hosted_attempt(transcriber, chunk: AudioChunk, previous_transcript: str | None = None) -> HostedAttemptOutcome:
    try:
        transcript = transcriber.transcribe(chunk, previous_transcript)
        status: Literal["success", "untranscribable"] = "success" if transcript.text.strip() else "untranscribable"
        return HostedAttemptOutcome(
            chunk=chunk,
            status=status,
            transcript=transcript,
            provider=getattr(transcriber, "provider", ""),
            model=getattr(transcriber, "model", ""),
        )
    except MalformedTranscriptionResponse as exc:
        return HostedAttemptOutcome(
            chunk=chunk,
            status="quality_failure",
            error=exc,
            provider=getattr(transcriber, "provider", ""),
            model=getattr(transcriber, "model", ""),
        )
    except Exception as exc:
        return HostedAttemptOutcome(
            chunk=chunk,
            status="transport_failure",
            error=exc,
            provider=getattr(transcriber, "provider", ""),
            model=getattr(transcriber, "model", ""),
        )


def transcribe_and_align_hosted(
    chunks,
    transcriber: FallbackTranscriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    workers: int,
    align_workers: int,
    recovery_max_split_depth: int = 0,
    recovery_temp_dir: Path | None = None,
    recovery_vad_session: VadSession | None = None,
    recovery_min_silence_ms: int = 400,
    recovery_speech_pad_ms: int = 200,
):
    ordered = sorted(chunks, key=lambda item: (item.start, item.end, item.index))
    pool = AlignmentPool(capped_align_workers(align_workers, len(ordered)), alignment_config, profiler)
    normal: dict[int, HostedAttemptOutcome] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as transcribe_pool:
        futures = {}
        for i, chunk in enumerate(ordered, start=1):
            if len(ordered) <= 10 or i <= 5 or i > len(ordered) - 5:
                print(f"Queueing transcription chunk {i}/{len(ordered)} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
            start = now()
            future = transcribe_pool.submit(hosted_attempt, transcriber.primary, chunk)
            futures[future] = (i, chunk, start)
        for future in as_completed(futures):
            i, chunk, start = futures[future]
            profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
            normal[chunk.index] = future.result()
            print(f"Transcription complete: {i}/{len(ordered)} [{chunk.start:.2f}-{chunk.end:.2f}s]", flush=True)

    resolved: dict[int, TranscriptChunk | None] = {}
    failed: list[TranscriptChunk] = []
    for position, chunk in enumerate(ordered):
        outcome = normal[chunk.index]
        final = outcome
        previous = resolved.get(ordered[position - 1].index) if position > 0 else None
        previous_text = previous.text if previous is not None and previous.text.strip() else None
        if outcome.status == "quality_failure":
            print(f"Warning: primary model returned bad output for chunk {chunk.index}. {outcome.error}", flush=True)
            if previous_text:
                print(f"Retrying hosted chunk {chunk.index} with preceding transcript context...", flush=True)
                final = hosted_attempt(transcriber.primary, chunk, previous_text)
            else:
                print(
                    f"Warning: contextual retry skipped for chunk {chunk.index}; preceding transcript unavailable.",
                    flush=True,
                )
        elif outcome.status == "transport_failure":
            print(
                f"Warning: primary transport failure for chunk {chunk.index}; routing directly to backup. {outcome.error}",
                flush=True,
            )

        needs_backup = (
            outcome.status == "transport_failure"
            or (outcome.status == "quality_failure" and (not previous_text or final.status not in {"success", "untranscribable"}))
        )
        if needs_backup and transcriber.fallback is not None:
            print(
                f"Invoking backup transcription for chunk {chunk.index}: "
                f"{transcriber.fallback.provider}:{transcriber.fallback.model}.",
                flush=True,
            )
            final = hosted_attempt(transcriber.fallback, chunk)

        if final.status == "success" and final.transcript is not None:
            resolved[chunk.index] = final.transcript
        elif final.status == "untranscribable":
            resolved[chunk.index] = None
        else:
            exc = final.error or RuntimeError("hosted transcription produced no usable output")
            recovered = _recover_hosted_chunk_with_split(
                transcriber,
                chunk,
                max_depth=recovery_max_split_depth,
                temp_dir=recovery_temp_dir,
                vad_session=recovery_vad_session,
                min_silence_ms=recovery_min_silence_ms,
                speech_pad_ms=recovery_speech_pad_ms,
            )
            if recovered is None or (not recovered.text and recovered.unrecovered):
                profiler.mark_error(chunk.index, exc)
                failed_item = failed_transcript(chunk, exc)
                failed.append(failed_item)
                resolved[chunk.index] = None
            elif recovered.text:
                print(f"Recovered hosted transcription chunk {chunk.index} with smaller audio segments.", flush=True)
                resolved[chunk.index] = TranscriptChunk(chunk, recovered.text)
                if recovered.unrecovered:
                    for failed_chunk, recovery_error in recovered.unrecovered:
                        profiler.mark_error(chunk.index, recovery_error)
                        failed.append(
                            TranscriptChunk(
                                chunk=failed_chunk,
                                text=FAILED_TRANSCRIPTION_TEXT,
                                error=str(recovery_error),
                            )
                        )
                    print(
                        f"Warning: retained the usable recovery text for hosted chunk {chunk.index}; "
                        f"{len(recovered.unrecovered)} smaller segment(s) remain untranscribed.",
                        flush=True,
                    )
            else:
                resolved[chunk.index] = None

    for chunk in ordered:
        transcript = resolved[chunk.index]
        if transcript is not None:
            pool.submit(transcript)
    print("Waiting for alignment workers...", flush=True)
    aligned = pool.close_and_collect()
    print_transcription_failure_summary(failed)
    return aligned, failed


def _recover_hosted_chunk_with_split(
    transcriber: FallbackTranscriber,
    chunk: AudioChunk,
    *,
    max_depth: int,
    temp_dir: Path | None,
    vad_session: VadSession | None,
    min_silence_ms: int,
    speech_pad_ms: int,
    depth: int = 0,
) -> HostedSplitRecovery | None:
    if depth >= max_depth:
        return None
    subchunks = split_chunk_with_tighter_vad(
        chunk,
        sample_rate=16000,
        temp_dir=temp_dir,
        keep_temp=temp_dir is not None,
        session=vad_session,
        recovery_min_silence_ms=min_silence_ms,
        recovery_speech_pad_ms=speech_pad_ms,
        max_pieces=2,
    )
    if len(subchunks) < 2:
        return None
    print(
        f"Retrying failed hosted chunk {chunk.index} [{chunk.start:.2f}-{chunk.end:.2f}s] "
        f"as {len(subchunks)} smaller segment(s), recovery depth {depth + 1}/{max_depth}.",
        flush=True,
    )
    parts: list[str] = []
    unrecovered: list[tuple[AudioChunk, Exception]] = []
    for subchunk in subchunks:
        outcome = hosted_attempt(transcriber.primary, subchunk)
        if outcome.status not in {"success", "untranscribable"} and transcriber.fallback is not None:
            outcome = hosted_attempt(transcriber.fallback, subchunk)
        if outcome.status == "success" and outcome.transcript is not None:
            parts.append(outcome.transcript.text)
            continue
        if outcome.status == "untranscribable":
            continue
        nested = _recover_hosted_chunk_with_split(
            transcriber,
            subchunk,
            max_depth=max_depth,
            temp_dir=temp_dir,
            vad_session=vad_session,
            min_silence_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
            depth=depth + 1,
        )
        if nested is None:
            unrecovered.append(
                (subchunk, outcome.error or RuntimeError("hosted recovery produced no usable output"))
            )
            continue
        if nested.text:
            parts.append(nested.text)
        unrecovered.extend(nested.unrecovered)
    return HostedSplitRecovery(
        text="".join(part.strip() for part in parts if part.strip()),
        unrecovered=unrecovered,
    )


def transcribe_one(transcriber, chunk, profiler: PipelineProfiler):
    start = now()
    try:
        transcript = transcriber.transcribe(chunk)
        profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
        return transcript
    except Exception as exc:
        profiler.mark_error(chunk.index, exc)
        return failed_transcript(chunk, exc)


def transcribe_and_align_parallel(
    chunks,
    transcriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    workers: int,
    align_workers: int,
):
    pool = AlignmentPool(capped_align_workers(align_workers, len(chunks)), alignment_config, profiler)
    failed: list[TranscriptChunk] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as transcribe_pool:
        futures = {}
        for i, chunk in enumerate(chunks, start=1):
            if len(chunks) <= 10 or i <= 5 or i > len(chunks) - 5:
                print(f"Queueing transcription chunk {i}/{len(chunks)} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
            futures[transcribe_pool.submit(transcribe_one, transcriber, chunk, profiler)] = (i, chunk)
        for future in as_completed(futures):
            i, chunk = futures[future]
            print(f"Transcription complete: {i}/{len(chunks)} [{chunk.start:.2f}-{chunk.end:.2f}s]", flush=True)
            transcript = future.result()
            if is_failed_transcript(transcript):
                failed.append(transcript)
                continue
            if not transcript.text:
                print(f"Warning: empty transcript for chunk {chunk.index}")
                continue
            pool.submit(transcript)
    print("Waiting for alignment workers...", flush=True)
    aligned = pool.close_and_collect()
    print_transcription_failure_summary(failed)
    return aligned, failed


def prepare_payload(transcriber, chunk, profiler: PipelineProfiler):
    start = now()
    payload = transcriber.prepare_payload(chunk)
    profiler.add_ms(chunk.index, "payload_prepare_ms", (now() - start) * 1000)
    return payload


def transcribe_and_align_server(
    chunks,
    transcriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    audio_prep_workers: int,
    align_workers: int,
):
    prep_futures: dict[int, Future] = {}
    next_to_submit = 0
    total = len(chunks)
    pool = AlignmentPool(capped_align_workers(align_workers, len(chunks)), alignment_config, profiler)
    failed: list[TranscriptChunk] = []
    previous_text: str | None = None
    with ThreadPoolExecutor(max_workers=audio_prep_workers) as prep_pool:
        while next_to_submit < min(audio_prep_workers, total):
            chunk = chunks[next_to_submit]
            prep_futures[chunk.index] = prep_pool.submit(prepare_payload, transcriber, chunk, profiler)
            next_to_submit += 1
        for i, chunk in enumerate(chunks, start=1):
            if chunk.index not in prep_futures:
                prep_futures[chunk.index] = prep_pool.submit(prepare_payload, transcriber, chunk, profiler)
            print(f"Transcribing chunk {i}/{total} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
            try:
                payload = prep_futures.pop(chunk.index).result()
                while next_to_submit < total and len(prep_futures) < audio_prep_workers:
                    upcoming = chunks[next_to_submit]
                    prep_futures[upcoming.index] = prep_pool.submit(prepare_payload, transcriber, upcoming, profiler)
                    next_to_submit += 1
                start = now()
                transcripts = [
                    TranscriptChunk(
                        chunk=chunk,
                        text=transcriber.transcribe_payload(chunk, payload, previous_text),
                    )
                ]
                profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
                transcripts = [item for item in transcripts if item.text.strip()]
                if not transcripts:
                    print(f"Warning: empty transcript for chunk {chunk.index}")
                    previous_text = None
                    continue
                for transcript in transcripts:
                    pool.submit(transcript)
                previous_text = "".join(item.text for item in transcripts)
            except Exception as exc:
                profiler.mark_error(chunk.index, exc)
                failed.append(failed_transcript(chunk, exc))
                previous_text = None
    print("Waiting for alignment workers...", flush=True)
    aligned = deduplicate_overlapping_aligned_chunks(pool.close_and_collect())
    print_transcription_failure_summary(failed)
    return aligned, failed


def deduplicate_overlapping_aligned_chunks(chunks: list[AlignedChunk]) -> list[AlignedChunk]:
    """Remove duplicate boundary text produced by independently aligned overlap audio."""
    ordered = sorted(chunks, key=lambda item: (item.chunk.start, item.chunk.end, item.chunk.index))
    if len(ordered) < 2:
        return ordered
    result: list[AlignedChunk] = [ordered[0]]
    for current in ordered[1:]:
        previous = result[-1]
        if previous.chunk.end <= current.chunk.start or not previous.tokens or not current.tokens:
            result.append(current)
            continue
        match = _aligned_overlap_match(previous, current)
        if match is None:
            fuzzy_seam = _aligned_fuzzy_overlap_seam(previous, current)
            if _trim_aligned_overlap_at_seam(previous, current, seam=fuzzy_seam):
                if not previous.tokens:
                    result.pop()
                if current.tokens:
                    result.append(current)
                continue
            result.append(current)
            continue
        size, keep = match
        if keep == "left":
            current.tokens = current.tokens[size:]
            if not current.tokens:
                continue
            current.text = "".join(token.text for token in current.tokens)
        else:
            previous.tokens = previous.tokens[:-size]
            if previous.tokens:
                previous.text = "".join(token.text for token in previous.tokens)
            else:
                result.pop()
        result.append(current)
    return result


def _aligned_overlap_match(left: AlignedChunk, right: AlignedChunk) -> tuple[int, str] | None:
    maximum = min(24, len(left.tokens), len(right.tokens))
    for size in range(maximum, 1, -1):
        left_tokens = left.tokens[-size:]
        right_tokens = right.tokens[:size]
        left_text = "".join(token.text for token in left_tokens)
        right_text = "".join(token.text for token in right_tokens)
        if re.sub(r"\s+", "", left_text) != re.sub(r"\s+", "", right_text):
            continue
        left_midpoint = (left_tokens[0].start + left_tokens[-1].end) / 2.0
        right_midpoint = (right_tokens[0].start + right_tokens[-1].end) / 2.0
        overlap_duration = max(
            0.0,
            min(left.chunk.end, right.chunk.end) - max(left.chunk.start, right.chunk.start),
        )
        if abs(left_midpoint - right_midpoint) > max(0.75, overlap_duration):
            continue
        left_edge_margin = max(0.0, left.chunk.end - left_midpoint)
        right_edge_margin = max(0.0, right_midpoint - right.chunk.start)
        return size, "left" if left_edge_margin >= right_edge_margin else "right"
    return None


def _aligned_fuzzy_overlap_seam(left: AlignedChunk, right: AlignedChunk) -> float | None:
    """Locate a shared phrase in the overlap without requiring equal chunk edges."""
    overlap_start = max(left.chunk.start, right.chunk.start)
    overlap_end = min(left.chunk.end, right.chunk.end)
    if overlap_end <= overlap_start:
        return None

    def overlap_characters(
        tokens: list[AlignedToken],
        *,
        after: float | None = None,
        before: float | None = None,
    ) -> tuple[list[str], list[int]]:
        characters: list[str] = []
        owners: list[int] = []
        for token_index, token in enumerate(tokens):
            midpoint = (token.start + token.end) / 2.0
            if after is not None and midpoint < after:
                continue
            if before is not None and midpoint > before:
                continue
            normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", token.text)).casefold()
            for character in normalized:
                characters.append(character)
                owners.append(token_index)
        return characters, owners

    left_chars, left_owners = overlap_characters(left.tokens, after=overlap_start - 0.25)
    right_chars, right_owners = overlap_characters(right.tokens, before=overlap_end + 0.25)
    if len(left_chars) < 3 or len(right_chars) < 3:
        return None
    matcher = SequenceMatcher(None, left_chars, right_chars, autojunk=False)
    best: tuple[int, float] | None = None
    for block in matcher.get_matching_blocks():
        if block.size < 3:
            continue
        left_first = left.tokens[left_owners[block.a]]
        left_last = left.tokens[left_owners[block.a + block.size - 1]]
        right_first = right.tokens[right_owners[block.b]]
        right_last = right.tokens[right_owners[block.b + block.size - 1]]
        left_midpoint = (left_first.start + left_last.end) / 2.0
        right_midpoint = (right_first.start + right_last.end) / 2.0
        if abs(left_midpoint - right_midpoint) > max(0.6, (overlap_end - overlap_start) * 0.75):
            continue
        seam = max(overlap_start, min(overlap_end, (left_midpoint + right_midpoint) / 2.0))
        score = block.size
        if best is None or score > best[0]:
            best = (score, seam)
    return best[1] if best is not None else None


def _trim_aligned_overlap_at_seam(
    left: AlignedChunk,
    right: AlignedChunk,
    *,
    seam: float | None = None,
) -> bool:
    """Assign non-identical overlap text to one side using absolute aligned time."""
    overlap_start = max(left.chunk.start, right.chunk.start)
    overlap_end = min(left.chunk.end, right.chunk.end)
    if overlap_end <= overlap_start:
        return False
    if seam is None:
        seam = (overlap_start + overlap_end) / 2.0
    else:
        seam = max(overlap_start, min(overlap_end, seam))
    left_tokens = [
        token
        for token in left.tokens
        if (token.start + token.end) / 2.0 <= seam
    ]
    right_tokens = [
        token
        for token in right.tokens
        if (token.start + token.end) / 2.0 > seam
    ]
    removed = len(left_tokens) < len(left.tokens) or len(right_tokens) < len(right.tokens)
    if not removed:
        return False
    left.tokens = left_tokens
    left.text = "".join(token.text for token in left_tokens)
    right.tokens = right_tokens
    right.text = "".join(token.text for token in right_tokens)
    return True


def capped_align_workers(configured_workers: int, segment_count: int) -> int:
    if segment_count <= 0:
        return 0
    return min(max(1, configured_workers), segment_count)


def failed_transcript(chunk, exc: Exception) -> TranscriptChunk:
    print(
        f"Warning: transcription failed for chunk {chunk.index} "
        f"[{chunk.start:.2f}-{chunk.end:.2f}s]; leaving blank and continuing. {exc}",
        flush=True,
    )
    return TranscriptChunk(chunk=chunk, text=FAILED_TRANSCRIPTION_TEXT, error=str(exc))


def is_failed_transcript(transcript: TranscriptChunk) -> bool:
    return transcript.text.strip().lower() == FAILED_TRANSCRIPTION_TEXT


def print_transcription_failure_summary(failed_transcripts: list[TranscriptChunk]) -> None:
    if not failed_transcripts:
        return
    details = ", ".join(f"{item.chunk.index} [{item.chunk.start:.2f}-{item.chunk.end:.2f}s]" for item in failed_transcripts)
    print(
        f"Transcription failed for {len(failed_transcripts)} chunk(s); "
        f"those ranges remain untranscribed: {details}",
        flush=True,
    )
