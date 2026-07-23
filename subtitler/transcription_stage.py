"""Audio preparation and normalized transcription pipeline stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .api_usage import ApiUsageLedger
from .audio import extract_audio, get_media_duration, load_mono_16k_wav
from .backends.existing_pipeline import ExistingPipelineBackend
from .errors import SubtitlerError
from .glossary import GlossaryEntry, find_glossary, load_glossary
from .models import AlignedChunk
from .profiling import PipelineProfiler
from .run_artifacts import write_aligned_text
from .run_context import RunContext
from .transcript_normalizer import backend_result_to_aligned_chunks
from .transcription_backend import BackendTranscriptResult, TranscriptionBackend, TranscriptionRequest
from .silence_cut import emit_frontend_event


@dataclass(frozen=True)
class TranscriptionStageOutcome:
    backend_result: BackendTranscriptResult
    aligned: list[AlignedChunk]
    glossary: list[GlossaryEntry]
    duration_sec: float
    cost_estimate_only: bool = False


def run_transcription_stage(
    context: RunContext,
    temp_dir: Path,
    api_usage: ApiUsageLedger,
    profiler: PipelineProfiler,
    *,
    project_dir: Path,
) -> TranscriptionStageOutcome:
    """Prepare audio, run the configured backend, and normalize its transcript."""
    config = context.config
    wav_path = temp_dir / "input_16k_mono.wav"
    duration = get_media_duration(context.input_path)
    print("Extracting mono 16 kHz audio...")
    extract_audio(
        context.input_path,
        wav_path,
        int(config["audio"]["track"]),
        duration=duration,
        progress_callback=progress_reporter("Audio extraction"),
    )
    samples, sample_rate = load_mono_16k_wav(wav_path)
    if duration <= 0:
        duration = len(samples) / sample_rate

    glossary_path = find_glossary(
        input_path=context.input_path,
        explicit=Path(context.args.glossary) if context.args.glossary else None,
        disabled=context.args.no_glossary,
        project_dir=project_dir,
    )
    glossary = load_glossary(glossary_path)
    if glossary:
        print(f"Loaded glossary entries: {len(glossary)}")

    backend = build_backend(config, api_usage, profiler)
    request = TranscriptionRequest(
        input_path=context.input_path,
        wav_path=wav_path,
        duration_sec=duration,
        sample_rate=sample_rate,
        language=config["backend"].get("language", "ja"),
        temp_dir=temp_dir,
        sidecar_base=context.artifacts.base,
        glossary=glossary,
        profile_enabled=context.diagnostics_enabled,
        workflow=context.args.workflow,
        metadata={
            "samples": samples,
            "stage_progress_reporter": stage_progress_reporter("VAD"),
            "control_event": emit_frontend_event if context.args.frontend_protocol == "stdio-v1" else None,
        },
    )
    backend_result = backend.transcribe(request)
    cost_estimate_only = backend_result.status == "partial" and any(
        item.code == "cost_estimate_only" for item in backend_result.diagnostics
    )
    if cost_estimate_only:
        return TranscriptionStageOutcome(
            backend_result=backend_result,
            aligned=[],
            glossary=glossary,
            duration_sec=duration,
            cost_estimate_only=True,
        )

    handle_backend_result_status(backend_result)
    aligned = backend_result_to_aligned_chunks(backend_result)
    if context.diagnostics_enabled and context.artifacts.aligned_text is not None:
        write_aligned_text(context.artifacts.aligned_text, aligned)
    return TranscriptionStageOutcome(
        backend_result=backend_result,
        aligned=aligned,
        glossary=glossary,
        duration_sec=duration,
    )


def build_backend(
    config: dict, api_usage: ApiUsageLedger, profiler: PipelineProfiler
) -> TranscriptionBackend:
    if config["backend"]["name"] == "existing-pipeline":
        return ExistingPipelineBackend(config, api_usage, profiler)
    raise SubtitlerError(f"Unknown backend: {config['backend']['name']}")


def handle_backend_result_status(result: BackendTranscriptResult) -> None:
    if result.status == "failed":
        raise SubtitlerError(
            "Transcription failed: selected speech produced no usable transcript segments. "
            "Review the transcription diagnostics and provider or local-server logs."
        )
    if result.status == "partial":
        failed_chunks = sum(item.code == "transcription_failed" for item in result.diagnostics)
        detail = f" ({failed_chunks} chunk(s) failed)" if failed_chunks else ""
        print(
            "Warning: transcription completed with a partial result"
            f"{detail}; continuing with the usable segments.",
            flush=True,
        )


def progress_reporter(label: str, step: int = 10) -> Callable[[float], None]:
    next_percent = {"value": step}

    def report(progress: float) -> None:
        while progress + 1e-9 >= next_percent["value"] and next_percent["value"] <= 100:
            print(f"{label} progress: {next_percent['value']}%", flush=True)
            next_percent["value"] += step

    return report


def stage_progress_reporter(label: str, step: int = 10) -> Callable[[str, float], None]:
    reporters: dict[str, Callable[[float], None]] = {}

    def report(stage: str, progress: float) -> None:
        if stage not in reporters:
            reporters[stage] = progress_reporter(f"{label} {stage}", step)
        reporters[stage](progress)

    return report
