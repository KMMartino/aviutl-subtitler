"""Existing subtitle modes composed from processing operations."""

from __future__ import annotations

import sys
import hashlib
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .api_usage import ApiUsageLedger
from .broll_stage import BrollStageRequest, broll_summary, run_broll_stage
from .config import project_root, validate_workflow_config
from .errors import SubtitlerError
from .operation_store import OperationStore
from .media_identity import fingerprint_source
from .review_exchange import ReviewStorage, emit_frontend_event
from .subtitle_checkpoint import decode_subtitle_plan, load_subtitle_checkpoint, preparation_signature, save_subtitle_checkpoint
from .transcription_stage import TranscriptionStageOutcome
from .transcript_normalizer import backend_result_to_aligned_chunks
from .media_export import ExoExportRequest, export_exo, prepare_source_layout
from .profiling import PipelineProfiler
from .run_artifacts import (
    format_elapsed as _format_elapsed,
    write_run_metadata as _write_run_metadata,
)
from .run_context import (
    CliArguments,
    prepare_run_context,
)
from .silence_cut import (
    build_cut_candidates,
    execute_silence_cut,
    write_silence_manifest,
)
from .silence_review import request_review
from .subtitle_stage import SubtitleStageRequest, run_subtitle_stage
from .transcription_stage import TranscriptionStageRequest, run_transcription_stage
from .glossary import find_glossary, load_glossary
from .transcription_backend import RawVadSpeechInterval
from .workflow_policy import workflow_definition
from .timed_text import TimedTextDocument, write_timed_text


def run_subtitle_workflow(args: CliArguments) -> int:
    if args.workflow == "hosted-long-stream":
        from .editorial_project_cli import main as run_markers
        output = Path(args.output) if args.output else Path(args.input).with_suffix(".exo")
        command = ["start", "--checkpoint", str(output.with_suffix(".json")),
                   "--source", args.input, "--exo", str(output)]
        if args.config:
            command.extend(["--config", args.config])
        if args.audio_track is not None:
            command.extend(["--audio-track", str(args.audio_track)])
        if args.sidecar_dir:
            command.extend(["--workspace", args.sidecar_dir])
        return run_markers(command)
    started = time.monotonic()
    pending_cut_video: Path | None = None
    output_committed = False
    try:
        context = prepare_run_context(args)
        input_path = context.input_path
        output_path = context.output_path
        config = context.config
        policy = workflow_definition(args.workflow).subtitles
        env_path = context.env_path
        loaded_env_keys = context.loaded_env_keys
        artifacts = context.artifacts
        sidecar_dir = artifacts.directory

        print(f"Input:  {input_path}")
        print(f"Output: {output_path}")
        print(f"Workflow: {args.workflow}")
        print(f"Config: {context.config_path}")
        print(f"Sidecars: {sidecar_dir if sidecar_dir is not None else 'disabled'}")

        api_usage = ApiUsageLedger()
        diagnostics_enabled = context.diagnostics_enabled
        profiler = PipelineProfiler(diagnostics_enabled, artifacts.profile)

        with tempfile.TemporaryDirectory(prefix="subtitler_") as temp_name:
            temp_dir = Path(temp_name)
            glossary = load_glossary(find_glossary(
                input_path=input_path,
                explicit=Path(args.glossary) if args.glossary else None,
                disabled=args.no_glossary,
                project_dir=project_root(),
            ))

            def on_speech_activity(intervals: list[RawVadSpeechInterval]) -> None:
                if args.frontend_protocol == "stdio-v1" and config["additional_settings"]["cut_silence_mode"] == "review":
                    emit_frontend_event(
                        "silence-candidates", workflow=args.workflow,
                        candidates=[candidate.to_frontend() for candidate in build_cut_candidates(intervals)],
                    )

            checkpoint_path = artifacts.base.with_suffix(".pending.json") if artifacts.base is not None else None
            signature = preparation_signature({
                "source": str(input_path.resolve()),
                "source_fingerprint": asdict(fingerprint_source(input_path)),
                "source_modified_ns": input_path.stat().st_mtime_ns,
                "output": str(output_path.resolve()),
                "config": {key: config[key] for key in ("backend", "audio", "vad", "alignment", "workflow", "cost")},
                "glossary": [asdict(entry) for entry in glossary],
                "transcript_artifact": hashlib.sha256(Path(args.transcript_artifact).read_bytes()).hexdigest() if args.transcript_artifact else None,
            }) if checkpoint_path is not None else ""
            subtitle_signature = preparation_signature({
                "subtitles": config["subtitles"], "cleanup": config["cleanup"], "policy": asdict(policy),
                "chapters": config["additional_settings"]["youtube_chapters"],
            })
            checkpoint = load_subtitle_checkpoint(
                checkpoint_path, signature=signature, source_path=input_path,
                audio_track=int(config["audio"]["track"]), subtitle_signature=subtitle_signature,
            ) if checkpoint_path is not None and not args.fresh_run else None
            validate_workflow_config(
                config, workflow=args.workflow,
                transcription_required=checkpoint is None and args.transcript_artifact is None,
                cleanup_required=not policy.raw_transcript and (checkpoint is None or checkpoint.subtitles is None),
            )
            if checkpoint is not None:
                document = checkpoint.transcript
                transcription = TranscriptionStageOutcome(
                    backend_result=document.backend,
                    aligned=backend_result_to_aligned_chunks(document.backend),
                    duration_sec=document.duration_sec, document_path=checkpoint.transcript_path,
                    revision_id=document.revision_id, reused=True,
                )
                api_usage.rows.extend(checkpoint.usage)
                print("Resuming saved subtitle plan; transcription and cleanup are already complete." if checkpoint.subtitles is not None
                      else "Resuming completed transcription; subtitle planning is next.", flush=True)
                on_speech_activity(document.backend.raw_vad_speech_intervals)
            else:
                transcription = run_transcription_stage(
                    TranscriptionStageRequest(
                        input_path=input_path,
                        config=config,
                        artifacts=artifacts,
                        glossary=glossary,
                        diagnostics_enabled=context.diagnostics_enabled,
                        on_speech_activity=on_speech_activity,
                        reuse_document=Path(args.transcript_artifact) if args.transcript_artifact else None,
                    ),
                    temp_dir,
                    api_usage,
                    profiler,
                )
            backend_result = transcription.backend_result
            backend_result.metadata["transcript_artifact"] = {
                "path": str(transcription.document_path) if transcription.document_path else None,
                "revision_id": transcription.revision_id,
                "reused": transcription.reused,
            }
            if transcription.cost_estimate_only:
                if artifacts.run_metadata is not None and artifacts.api_usage is not None:
                    _write_run_metadata(
                        artifacts.run_metadata,
                        args,
                        config,
                        env_path,
                        loaded_env_keys,
                        backend_result,
                        api_usage,
                        artifacts.api_usage,
                        elapsed_run_seconds=time.monotonic() - started,
                        argv=sys.argv[1:],
                    )
                print("Cost estimate only; exiting before transcription.", flush=True)
                return 0
            aligned = transcription.aligned
            duration = transcription.duration_sec

            reusable = backend_result.status == "ok" and not any(
                item.code == "transcription_failed" for item in backend_result.diagnostics
            )
            if checkpoint is None and checkpoint_path is not None and transcription.document_path is not None and reusable:
                checkpoint = save_subtitle_checkpoint(
                    checkpoint_path, signature=signature, transcript_path=transcription.document_path,
                    subtitles=None, usage=api_usage,
                )
            if checkpoint is not None and checkpoint.subtitles is not None:
                subtitle_result = checkpoint.subtitles
            else:
                def plan_subtitles():
                    return run_subtitle_stage(
                        SubtitleStageRequest(
                            config=config, artifacts=artifacts,
                            diagnostics_enabled=context.diagnostics_enabled,
                            sidecars_enabled=context.sidecars_enabled,
                            raw_transcript=policy.raw_transcript,
                            generate_chapters=policy.allow_chapters and config["additional_settings"]["youtube_chapters"],
                        ), aligned, glossary, api_usage,
                    )
                if checkpoint is not None:
                    if checkpoint.subtitle_signature is not None:
                        # A new subtitle plan creates a successor revision; carry the
                        # old downstream attempt costs into that revision once.
                        OperationStore(checkpoint.transcript_path.parent / "broll-operations", checkpoint.revision_id, api_usage)
                    operations = OperationStore(checkpoint.transcript_path.parent / "subtitle-operations", checkpoint.revision_id, api_usage)
                    subtitle_result = operations.execute("plan_subtitles", 1, {"signature": subtitle_signature},
                                                         lambda: asdict(plan_subtitles()), decode_subtitle_plan)
                    checkpoint = save_subtitle_checkpoint(
                        checkpoint.path, signature=signature, transcript_path=checkpoint.transcript_path,
                        subtitles=subtitle_result, usage=api_usage, subtitle_signature=subtitle_signature,
                    )
                    print(f"Workflow checkpoint: {checkpoint.path}", flush=True)
                else:
                    subtitle_result = plan_subtitles()
            subtitles = subtitle_result.subtitles
            chapter_markers = subtitle_result.chapter_markers
            mistranscription_markers = subtitle_result.mistranscription_markers

            if artifacts.base is not None:
                write_timed_text(
                    artifacts.base.with_suffix(".subtitles.json"),
                    TimedTextDocument.from_subtitles(
                        subtitles,
                        source_path=input_path,
                        audio_track=int(config["audio"]["track"]),
                        raw_transcript=policy.raw_transcript,
                        input_revision_id=transcription.revision_id,
                        complete=backend_result.status == "ok" and not any(
                            item.code == "transcription_failed" for item in backend_result.diagnostics
                        ),
                    ),
                )

            layout = prepare_source_layout(input_path, config["exo"])
            settings = layout.settings

            cut_mode = config["additional_settings"]["cut_silence_mode"]
            render_cut_video = bool(config["additional_settings"].get("render_cut_video", False))
            raw_vad_intervals = backend_result.raw_vad_speech_intervals
            cut_candidates = build_cut_candidates(raw_vad_intervals) if cut_mode != "off" else []
            review_result = request_review(
                cut_candidates, args.frontend_protocol,
                ReviewStorage(checkpoint.path.with_suffix(".silence-review.json"), checkpoint.revision_id)
                if checkpoint is not None else None,
            ) if cut_mode == "review" and cut_candidates else None
            cut_outcome = execute_silence_cut(
                mode=cut_mode,
                candidates=cut_candidates,
                raw_intervals=raw_vad_intervals,
                subtitles=subtitles,
                chapter_markers=chapter_markers,
                qa_markers=mistranscription_markers,
                duration_sec=duration,
                input_path=input_path,
                exo_path=output_path,
                encoder_preset=args.cut_silence_encoder,
                render_cut_video=render_cut_video,
                project_fps=settings.rate,
                review_result=review_result,
            )
            pending_cut_video = cut_outcome.cut_video_path
            subtitles = cut_outcome.subtitles
            chapter_markers = cut_outcome.chapter_markers
            mistranscription_markers = cut_outcome.qa_markers
            duration = cut_outcome.duration_sec
            backend_result.metadata["silence_cut"] = {
                "mode": cut_mode,
                "candidate_count": len(cut_candidates),
                "accepted_cut_count": len(cut_outcome.accepted_cuts),
                "removed_duration_sec": sum(end - start for start, end in cut_outcome.accepted_cuts),
                "output_strategy": cut_outcome.output_strategy,
                "media_source_path": str(cut_outcome.media_source_path) if cut_outcome.media_source_path else None,
                "media_segment_count": len(cut_outcome.media_plan.segments) if cut_outcome.media_plan else 0,
                "frame_rate_mode": cut_outcome.frame_rate_mode,
                "cut_video_path": str(cut_outcome.cut_video_path) if cut_outcome.cut_video_path else None,
                "omitted_streams": cut_outcome.omitted_streams,
            }
            if artifacts.silence_cuts is not None and cut_mode != "off":
                write_silence_manifest(
                    artifacts.silence_cuts,
                    raw_intervals=raw_vad_intervals,
                    candidates=cut_candidates,
                    outcome=cut_outcome,
                    encoder_preset=args.cut_silence_encoder,
                    project_fps=settings.rate,
                )
            if cut_mode != "off":
                if cut_outcome.cut_video_path is not None:
                    print(f"Cut video: {cut_outcome.cut_video_path}", flush=True)
                    if cut_outcome.omitted_streams:
                        print(f"Warning: Cut video omitted {', '.join(cut_outcome.omitted_streams)}.", flush=True)
                elif cut_outcome.output_strategy == "exo-source":
                    segment_count = len(cut_outcome.media_plan.segments) if cut_outcome.media_plan else 0
                    print(
                        f"EXO silence cutting: {segment_count} source video/audio segment(s); "
                        "no cut video was rendered.",
                        flush=True,
                    )
                else:
                    print("No silence cuts were selected; no media output was created.", flush=True)

            broll_placements = []
            broll_mode = config["additional_settings"].get("broll_mode", "off")
            if broll_mode != "off":
                broll_outcome = run_broll_stage(
                    BrollStageRequest(
                        mode=broll_mode,
                        config=config,
                        source_path=input_path,
                        source_cuts=tuple(cut_outcome.accepted_cuts),
                        database_path=Path(args.media_library_db) if args.media_library_db else None,
                        subtitles=subtitles,
                        settings=settings,
                        frontend_protocol=args.frontend_protocol,
                        sidecar_path=artifacts.broll_plan,
                        revision_directory=checkpoint.transcript_path.parent if checkpoint else None,
                        input_revision=checkpoint.revision_id if checkpoint else "",
                    ),
                    api_usage,
                )
                broll_placements = broll_outcome.placements
                backend_result.metadata["broll"] = broll_summary(broll_outcome, broll_mode)

            profiler.write()
            if artifacts.api_usage is not None:
                api_usage.write_csv(artifacts.api_usage)
            _print_api_cost_summary(api_usage)
            if artifacts.run_metadata is not None and artifacts.api_usage is not None:
                _write_run_metadata(
                    artifacts.run_metadata,
                    args,
                    config,
                    env_path,
                    loaded_env_keys,
                    backend_result,
                    api_usage,
                    artifacts.api_usage,
                    elapsed_run_seconds=time.monotonic() - started,
                    argv=sys.argv[1:],
                )

            export_exo(ExoExportRequest(
                source_path=input_path,
                output_path=output_path,
                duration_sec=duration,
                layout=layout,
                subtitles=subtitles,
                chapter_markers=chapter_markers,
                qa_markers=mistranscription_markers,
                media_plan=cut_outcome.media_plan,
                broll_placements=broll_placements,
            ))
            output_committed = True
            if checkpoint is not None:
                checkpoint.finish()
            if cut_outcome.cut_video_path is not None and args.frontend_protocol:
                emit_frontend_event("silence-cut-output", path=str(cut_outcome.cut_video_path))

        print(f"Successfully generated: {output_path}")
        print(f"Total subtitles: {len(subtitles)}")
        print(f"Run time: {_format_elapsed(time.monotonic() - started)}")
        return 0
    except SubtitlerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    finally:
        if pending_cut_video is not None and not output_committed:
            pending_cut_video.unlink(missing_ok=True)


def _print_api_cost_summary(api_usage: ApiUsageLedger) -> None:
    if not api_usage.rows:
        return
    print("Hosted API cost summary:", flush=True)
    for provider, cost in sorted(api_usage.total_cost_by_provider().items()):
        print(f"  {provider}: ${cost:.4f}", flush=True)
    print(f"  total: ${api_usage.total_cost_usd:.4f}", flush=True)
