"""Audio preparation and normalized transcription pipeline stage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .api_usage import ApiUsageLedger
from .audio import extract_audio, get_media_duration, load_mono_16k_wav
from .backends.existing_pipeline import ExistingPipelineBackend
from .errors import SubtitlerError
from .glossary import GlossaryEntry
from .models import AlignedChunk
from .profiling import PipelineProfiler
from .run_artifacts import RunArtifactPaths, write_aligned_text, write_aligned_tokens
from .transcript_document import create_transcript_document, load_transcript_document, write_transcript_document
from .transcript_normalizer import backend_result_to_aligned_chunks
from .transcription_backend import BackendTranscriptResult, RawVadSpeechInterval, TranscriptionBackend, TranscriptionRequest


@dataclass(frozen=True)
class TranscriptionStageRequest:
    """Processing inputs independent of CLI arguments and desktop transport."""

    input_path: Path
    config: dict[str, Any]
    artifacts: RunArtifactPaths
    glossary: list[GlossaryEntry]
    diagnostics_enabled: bool = False
    on_speech_activity: Callable[[list[RawVadSpeechInterval]], None] | None = None
    reuse_document: Path | None = None


@dataclass(frozen=True)
class TranscriptionStageOutcome:
    backend_result: BackendTranscriptResult
    aligned: list[AlignedChunk]
    duration_sec: float
    cost_estimate_only: bool = False
    document_path: Path | None = None
    revision_id: str | None = None
    reused: bool = False


def run_transcription_stage(
    inputs: TranscriptionStageRequest,
    temp_dir: Path,
    api_usage: ApiUsageLedger,
    profiler: PipelineProfiler,
) -> TranscriptionStageOutcome:
    """Prepare audio, run the configured backend, and normalize its transcript."""
    if inputs.reuse_document is not None:
        document = load_transcript_document(inputs.reuse_document)
        document.require_reusable(inputs.input_path, int(inputs.config["audio"]["track"]))
        print(f"Reusing aligned transcript: {inputs.reuse_document}; transcription and alignment skipped.", flush=True)
        if inputs.on_speech_activity is not None:
            inputs.on_speech_activity(document.backend.raw_vad_speech_intervals)
        result, duration = document.backend, document.duration_sec
    else:
        before = inputs.input_path.stat()
        result, duration = _transcribe_source(inputs, temp_dir, api_usage, profiler)
        estimate_only = result.status == "partial" and any(item.code == "cost_estimate_only" for item in result.diagnostics)
        if estimate_only:
            return TranscriptionStageOutcome(result, [], duration, cost_estimate_only=True)
        handle_backend_result_status(result)
        after = inputs.input_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SubtitlerError("Source media changed during transcription; retry with the finished recording")
        document = create_transcript_document(
            source_path=inputs.input_path,
            audio_track=int(inputs.config["audio"]["track"]),
            duration_sec=duration,
            backend=result,
            settings={
                **{key: inputs.config.get(key, {}) for key in ("backend", "audio", "vad", "alignment", "workflow", "cleanup")},
                "glossary": [asdict(entry) for entry in inputs.glossary],
            },
        )
    document_path = inputs.reuse_document
    if inputs.artifacts.base is not None:
        document_path = inputs.artifacts.base.with_suffix(".transcript.json")
        write_transcript_document(document_path, document)
    aligned = backend_result_to_aligned_chunks(result)
    if inputs.diagnostics_enabled and inputs.artifacts.aligned_text is not None:
        write_aligned_text(inputs.artifacts.aligned_text, aligned)
    if inputs.artifacts.aligned_tokens is not None:
        write_aligned_tokens(inputs.artifacts.aligned_tokens, aligned)
    return TranscriptionStageOutcome(
        result, aligned, duration, document_path=document_path,
        revision_id=document.revision_id, reused=inputs.reuse_document is not None,
    )


def _transcribe_source(
    inputs: TranscriptionStageRequest, temp_dir: Path, api_usage: ApiUsageLedger, profiler: PipelineProfiler,
) -> tuple[BackendTranscriptResult, float]:
    config = inputs.config
    wav_path = temp_dir / "input_16k_mono.wav"
    duration = get_media_duration(inputs.input_path)
    print("Extracting mono 16 kHz audio...")
    extract_audio(
        inputs.input_path,
        wav_path,
        int(config["audio"]["track"]),
        duration=duration,
        progress_callback=progress_reporter("Audio extraction"),
    )
    samples, sample_rate = load_mono_16k_wav(wav_path)
    if duration <= 0:
        duration = len(samples) / sample_rate

    glossary = inputs.glossary
    if glossary:
        print(f"Loaded glossary entries: {len(glossary)}")

    backend = build_backend(config, api_usage, profiler)
    request = TranscriptionRequest(
        input_path=inputs.input_path,
        wav_path=wav_path,
        duration_sec=duration,
        sample_rate=sample_rate,
        language=config["backend"].get("language", "ja"),
        temp_dir=temp_dir,
        sidecar_base=inputs.artifacts.base,
        glossary=glossary,
        profile_enabled=inputs.diagnostics_enabled,
        metadata={
            "samples": samples,
            "stage_progress_reporter": stage_progress_reporter("VAD"),
            "on_speech_activity": inputs.on_speech_activity,
        },
    )
    return backend.transcribe(request), duration


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
