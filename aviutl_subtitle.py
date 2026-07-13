#!/usr/bin/env python3
"""Workflow runner for AviUtl EXO subtitle generation."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from subtitler.api_usage import ApiUsageLedger
from subtitler.config import WORKFLOWS
from subtitler.errors import SubtitlerError
from subtitler.exo import generate_exo_file, write_exo
from subtitler.models import ExoSettings
from subtitler.profiling import PipelineProfiler
from subtitler.run_artifacts import (
    build_youtube_chapter_markers,
    flag_possible_mistranscriptions,
    format_elapsed as _format_elapsed,
    write_final_subtitle_text,
    write_run_metadata as _write_run_metadata,
)
from subtitler.run_context import (
    CliArguments,
    configure_alignment_offline_mode,
    default_output_path,
    prepare_run_context,
)
from subtitler.subtitle_stage import (
    build_refiner,
    count_progress_reporter,
    default_chain_split_workers,
    default_cleanup_window,
    default_cleanup_workers,
    run_subtitle_stage,
)
from subtitler.transcription_backend import BackendTranscriptResult
from subtitler.transcription_stage import (
    build_backend,
    handle_backend_result_status,
    run_transcription_stage,
)


def parse_args() -> CliArguments:
    parser = argparse.ArgumentParser(
        description="Generate AviUtl .exo subtitles using one of the supported workflows.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Input audio or video file")
    parser.add_argument(
        "--workflow",
        choices=sorted(WORKFLOWS),
        default="local",
        help="Supported workflow to run",
    )
    parser.add_argument("--output", "-o", help="Output .exo file")
    parser.add_argument("--config", help="Workflow config JSON. Defaults to configs/<workflow>.json")
    parser.add_argument("--env-file", default=".env", help="Dotenv-style API key file")
    parser.add_argument("--profile", action="store_true", help="Write diagnostics even if config disables them")
    parser.add_argument("--audio-track", type=int, help="Override config audio track")
    parser.add_argument("--sidecar-dir", help="Diagnostics/intermediate output directory")
    parser.add_argument("--no-sidecars", action="store_true", help="Do not create diagnostic or intermediate sidecar files")
    parser.add_argument("--glossary", help="Glossary text file. Defaults to auto-discovery beside input or project")
    parser.add_argument("--no-glossary", action="store_true", help="Disable glossary loading")
    return CliArguments(**vars(parser.parse_args()))


def main() -> int:
    started = time.monotonic()
    args = parse_args()
    try:
        context = prepare_run_context(args)
        input_path = context.input_path
        output_path = context.output_path
        config = context.config
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
            transcription = run_transcription_stage(
                context,
                temp_dir,
                api_usage,
                profiler,
                project_dir=Path(__file__).resolve().parent,
            )
            backend_result = transcription.backend_result
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
            glossary = transcription.glossary
            duration = transcription.duration_sec

            subtitle_result = run_subtitle_stage(
                context,
                aligned,
                glossary,
                api_usage,
                refiner_factory=_build_refiner,
            )
            subtitles = subtitle_result.subtitles
            chapter_markers = subtitle_result.chapter_markers
            mistranscription_markers = subtitle_result.mistranscription_markers

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

            exo_cfg = config["exo"]
            settings = ExoSettings(
                width=int(exo_cfg["width"]),
                height=int(exo_cfg["height"]),
                rate=int(exo_cfg["fps"]),
                font=exo_cfg["font"],
                font_size=int(exo_cfg["font_size"]),
                y_position=float(exo_cfg["y_position"]),
            )
            content = generate_exo_file(
                subtitles,
                settings,
                duration,
                insert_initial_empty=True,
                chapter_markers=chapter_markers,
                mistranscription_markers=mistranscription_markers,
            )
            write_exo(output_path, content)

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


def _build_backend(config: dict, api_usage: ApiUsageLedger, profiler: PipelineProfiler):
    """Compatibility wrapper for callers of the former CLI-local helper."""
    return build_backend(config, api_usage, profiler)


def _configure_alignment_offline_mode(alignment: dict) -> bool:
    """Compatibility wrapper for callers of the former CLI-local helper."""
    return configure_alignment_offline_mode(alignment)


def _handle_backend_result_status(result: BackendTranscriptResult) -> None:
    """Compatibility wrapper for callers of the former CLI-local helper."""
    handle_backend_result_status(result)


def _build_refiner(config: dict, glossary, api_usage: ApiUsageLedger, sidecar_base: Path | None):
    """Compatibility wrapper and test seam for the extracted subtitle stage."""
    return build_refiner(config, glossary, api_usage, sidecar_base)


def _write_final_subtitle_text(path: Path, subtitles) -> None:
    write_final_subtitle_text(path, subtitles)


def _build_youtube_chapter_markers(subtitles, refiner, output_path: Path | None):
    return build_youtube_chapter_markers(subtitles, refiner, output_path)


def _flag_possible_mistranscriptions(subtitles, refiner, output_path: Path):
    return flag_possible_mistranscriptions(subtitles, refiner, output_path)


def _default_output_path(input_path: Path, workflow: str) -> Path:
    """Compatibility wrapper for callers of the former CLI-local helper."""
    return default_output_path(input_path, workflow)


def _default_cleanup_window(config: dict) -> int:
    return default_cleanup_window(config)


def _default_cleanup_workers(config: dict) -> int:
    return default_cleanup_workers(config)


def _default_chain_split_workers(config: dict) -> int:
    return default_chain_split_workers(config)


def _count_progress_reporter(step: int = 10):
    return count_progress_reporter(step)


def _print_api_cost_summary(api_usage: ApiUsageLedger) -> None:
    if not api_usage.rows:
        return
    print("Hosted API cost summary:", flush=True)
    for provider, cost in sorted(api_usage.total_cost_by_provider().items()):
        print(f"  {provider}: ${cost:.4f}", flush=True)
    print(f"  total: ${api_usage.total_cost_usd:.4f}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
