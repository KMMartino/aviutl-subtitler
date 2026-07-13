"""Existing Silero VAD -> ASR -> CTC alignment backend."""

from __future__ import annotations

import math
import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from subtitler.aligner import ctc_language_code, is_japanese_language
from subtitler.alignment_pool import AlignmentConfig, AlignmentPool
from subtitler.api_costs import estimate_run_cost
from subtitler.api_usage import ApiUsageLedger
from subtitler.errors import SubtitlerError
from subtitler.external_transcribers import FallbackTranscriber, GeminiTranscriber, OpenAITranscriber
from subtitler.models import AlignedChunk, AudioChunk, TranscriptChunk
from subtitler.profiling import PipelineProfiler, now
from subtitler.transcriber import ServerGemmaTranscriber
from subtitler.transcription_backend import (
    BackendCapability,
    BackendDiagnostic,
    BackendStatus,
    BackendTranscriptResult,
    SpeechRegion,
    TranscriptSegment,
    TranscriptToken,
    TranscriptionRequest,
)
from subtitler.vad import VadSession, segment_speech_with_groups, select_high_activation_chunks


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
        chunks, vad_groups = segment_speech_with_groups(
            samples=request.metadata["samples"],
            sample_rate=request.sample_rate,
            max_chunk_sec=float(vad_cfg["max_chunk_sec"]),
            min_speech_sec=float(vad_cfg["min_speech_sec"]),
            min_silence_ms=int(vad_cfg["min_silence_ms"]),
            speech_pad_ms=int(vad_cfg["speech_pad_ms"]),
            cleanup_group_max_sec=cleanup_group_max_sec,
            temp_dir=request.temp_dir,
            keep_temp=True,
            progress_callback=request.metadata.get("stage_progress_reporter"),
            session=vad_session,
        )
        print(
            f"VAD chunks: {len(chunks)} fine, {len(vad_groups)} cleanup group(s) "
            f"(cleanup_group_max_sec={cleanup_group_max_sec:.2f})",
            flush=True,
        )
        selection = build_speech_selection(workflow_cfg, chunks, request.duration_sec)

        if request.profile_enabled and request.sidecar_base is not None:
            write_vad_selection(request.sidecar_base.with_suffix(".vad_selection.csv"), chunks, selection.selected_chunks)
            write_vad_selection(request.sidecar_base.with_suffix(".vad_groups.csv"), vad_groups, vad_groups)

        estimated_api_cost = estimate_backend_run_cost(self.config, selection.selected_speech_seconds)
        print(
            "Estimated hosted API cost: "
            f"${estimated_api_cost:.4f} "
            f"(transcribed_speech={selection.selected_speech_seconds / 60.0:.2f} min, "
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
                diagnostics=[
                    BackendDiagnostic(
                        level="info",
                        message=f"Estimated hosted API cost: ${estimated_api_cost:.4f}",
                        code="cost_estimate_only",
                    )
                ],
                capabilities=self.capabilities,
                metadata=_backend_metadata(self.config, selection, estimated_api_cost),
            )
        enforce_cost_guard(self.config, estimated_api_cost)

        for chunk in selection.selected_chunks:
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
                chunks=selection.selected_chunks,
                transcriber=transcriber,
                alignment_config=config,
                profiler=self.profiler,
                audio_prep_workers=max(1, int(backend_cfg["audio_prep_workers"])),
                align_workers=max(1, align_workers),
                transcription_workers=max(1, transcription_workers(self.config)),
            )
        finally:
            close = getattr(transcriber, "close", None)
            if close is not None:
                close()

        segments = aligned_chunks_to_segments(aligned, request.language)
        status = transcription_result_status(
            selected_chunk_count=len(selection.selected_chunks),
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
            diagnostics=[
                BackendDiagnostic(
                    level="error" if status == "failed" else "warning",
                    message=f"Transcription failed for chunk {item.chunk.index}",
                    region_index=item.chunk.index,
                    code="transcription_failed",
                )
                for item in failed_transcripts
            ],
            capabilities=self.capabilities,
            metadata=_backend_metadata(self.config, selection, estimated_api_cost),
        )

    def _build_transcriber(self, request: TranscriptionRequest, vad_session: VadSession | None = None):
        backend_cfg = self.config["backend"]
        name = backend_cfg["transcriber"]
        model = transcription_model(self.config)
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
                ),
                self._build_fallback_transcriber(request),
            )
        if name == "openai":
            return FallbackTranscriber(
                OpenAITranscriber(
                    model=model,
                    temp_dir=request.temp_dir,
                    usage=self.api_usage,
                    glossary=request.glossary,
                    language=request.language,
                ),
                self._build_fallback_transcriber(request),
            )
        raise SubtitlerError(f"Unknown existing-pipeline transcriber: {name}")

    def _build_fallback_transcriber(self, request: TranscriptionRequest):
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
            )
        if name == "openai":
            return OpenAITranscriber(
                model=model,
                temp_dir=request.temp_dir,
                usage=self.api_usage,
                glossary=request.glossary,
                language=request.language,
                timeout_scale=2.0,
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


def _backend_metadata(config: dict[str, Any], selection: SpeechSelection, estimated_api_cost: float) -> dict[str, Any]:
    return {
        "transcriber": config["backend"]["transcriber"],
        "selected_speech_seconds": selection.selected_speech_seconds,
        "total_speech_seconds": selection.total_speech_seconds,
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


def select_transcription_chunks(workflow_cfg: dict[str, Any], chunks: list[AudioChunk], media_duration_sec: float) -> list[AudioChunk]:
    if workflow_cfg["mode"] != "long-stream":
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
):
    if hasattr(transcriber, "prepare_payload") and hasattr(transcriber, "transcribe_payload"):
        return transcribe_and_align_server(chunks, transcriber, alignment_config, profiler, audio_prep_workers, align_workers)
    if transcription_workers > 1:
        return transcribe_and_align_parallel(chunks, transcriber, alignment_config, profiler, transcription_workers, align_workers)

    pool = AlignmentPool(align_workers, alignment_config, profiler)
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
    pool = AlignmentPool(align_workers, alignment_config, profiler)
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
    pool = AlignmentPool(align_workers, alignment_config, profiler)
    failed: list[TranscriptChunk] = []
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
                text = transcriber.transcribe_payload(chunk, payload)
                profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
                if not text:
                    print(f"Warning: empty transcript for chunk {chunk.index}")
                    continue
                pool.submit(TranscriptChunk(chunk=chunk, text=text))
            except Exception as exc:
                profiler.mark_error(chunk.index, exc)
                failed.append(failed_transcript(chunk, exc))
    print("Waiting for alignment workers...", flush=True)
    aligned = pool.close_and_collect()
    print_transcription_failure_summary(failed)
    return aligned, failed


def failed_transcript(chunk, exc: Exception) -> TranscriptChunk:
    print(
        f"Warning: transcription failed for chunk {chunk.index} "
        f"[{chunk.start:.2f}-{chunk.end:.2f}s]; leaving blank and continuing. {exc}",
        flush=True,
    )
    return TranscriptChunk(chunk=chunk, text=FAILED_TRANSCRIPTION_TEXT)


def is_failed_transcript(transcript: TranscriptChunk) -> bool:
    return transcript.text.strip().lower() == FAILED_TRANSCRIPTION_TEXT


def print_transcription_failure_summary(failed_transcripts: list[TranscriptChunk]) -> None:
    if not failed_transcripts:
        return
    details = ", ".join(f"{item.chunk.index} [{item.chunk.start:.2f}-{item.chunk.end:.2f}s]" for item in failed_transcripts)
    print(
        f"Transcription failed for {len(failed_transcripts)} chunk(s); left blank and continued: {details}",
        flush=True,
    )
