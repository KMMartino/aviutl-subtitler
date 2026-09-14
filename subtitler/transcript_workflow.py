"""Full transcription and raw text planning, reusable without the subtitle CLI."""

from __future__ import annotations

import copy
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .config import validate_workflow_config
from .errors import SubtitlerError
from .glossary import GlossaryEntry
from .profiling import PipelineProfiler
from .run_artifacts import build_run_artifact_paths
from .run_context import configure_alignment_offline_mode
from .subtitle_stage import SubtitleStageRequest, run_subtitle_stage
from .timed_text import TimedTextDocument, write_timed_text
from .transcription_stage import TranscriptionStageRequest, run_transcription_stage


@dataclass(frozen=True)
class TranscriptWorkflowResult:
    document: TimedTextDocument
    document_path: Path
    transcript_path: Path
    api_usage_path: Path
    api_cost_usd: float


def run_transcript_workflow(
    *,
    source_path: Path,
    config: dict[str, Any],
    workspace: Path,
    audio_track: int,
    glossary: list[GlossaryEntry],
    reuse_document: Path | None = None,
    workflow: str = "hosted-long-stream",
) -> TranscriptWorkflowResult:
    """Produce complete factual text without rendering media or generating EXO."""
    effective = copy.deepcopy(config)
    effective["audio"]["track"] = audio_track
    validate_workflow_config(effective, workflow=workflow, transcription_required=reuse_document is None, cleanup_required=False)
    configure_alignment_offline_mode(effective["alignment"])
    artifacts = build_run_artifact_paths(source_path, Path("transcript"), enabled=True, directory=workspace)
    usage = ApiUsageLedger()
    profiler = PipelineProfiler(True, artifacts.profile)
    usage_path = workspace / "transcript.api_usage.csv"
    try:
        with tempfile.TemporaryDirectory(prefix="subtitler-transcript-") as temporary:
            transcription = run_transcription_stage(
                TranscriptionStageRequest(
                    source_path, effective, artifacts, glossary, diagnostics_enabled=True, reuse_document=reuse_document,
                ),
                Path(temporary),
                usage,
                profiler,
            )
        if transcription.cost_estimate_only:
            raise SubtitlerError("Cost estimate only: editorial analysis requires a completed transcription")
        failed = [item for item in transcription.backend_result.diagnostics if item.code == "transcription_failed"]
        if transcription.backend_result.status != "ok" or failed:
            groups = sorted({item.region_index for item in failed if item.region_index is not None})
            detail = f" in audio group(s) {', '.join(map(str, groups[:5]))}" if groups else ""
            raise SubtitlerError(
                f"Transcription left unresolved audio{detail} for {source_path.name}; resume from transcription. "
                "Semantic analysis cannot use an incomplete transcript."
            )
        planned = run_subtitle_stage(
            SubtitleStageRequest(effective, artifacts, diagnostics_enabled=True, raw_transcript=True),
            transcription.aligned,
            glossary,
            usage,
        )
        document = TimedTextDocument.from_subtitles(
            planned.subtitles,
            source_path=source_path,
            audio_track=audio_track,
            raw_transcript=True,
            complete=True,
            input_revision_id=transcription.revision_id,
        )
        document_path = workspace / "transcript.subtitles.json"
        write_timed_text(document_path, document)
        return TranscriptWorkflowResult(
            document=document,
            document_path=document_path,
            transcript_path=workspace / "transcript.transcript.json",
            api_usage_path=usage_path,
            api_cost_usd=usage.total_cost_usd,
        )
    except Exception as exc:
        # The editorial runner records this even when no reusable output exists.
        setattr(exc, "editorial_failure_output", {
            "api_cost_usd": usage.total_cost_usd,
            "api_usage": [row.__dict__ for row in usage.rows],
            "api_usage_path": str(usage_path),
        })
        raise
    finally:
        profiler.write()
        usage.write_csv(usage_path)
