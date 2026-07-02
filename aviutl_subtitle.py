#!/usr/bin/env python3
"""Workflow runner for AviUtl EXO subtitle generation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from subtitler.api_usage import ApiUsageLedger
from subtitler.audio import extract_audio, get_media_duration, load_mono_16k_wav
from subtitler.backends.existing_pipeline import ExistingPipelineBackend
from subtitler.config import WORKFLOWS, default_config_path, load_workflow_config, validate_workflow_config
from subtitler.env import load_env_file
from subtitler.errors import SubtitlerError
from subtitler.exo import generate_exo_file, write_exo
from subtitler.external_refiners import GeminiTextRefiner, OpenAITextRefiner
from subtitler.glossary import find_glossary, load_glossary
from subtitler.models import ExoMarker, ExoSettings
from subtitler.profiling import PipelineProfiler
from subtitler.subtitle_planner import build_grouped_subtitles
from subtitler.text_refiner import LlamaServerTextRefiner
from subtitler.transcript_normalizer import backend_result_to_aligned_chunks
from subtitler.transcription_backend import TranscriptionRequest


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def main() -> int:
    started = time.monotonic()
    args = parse_args()
    try:
        input_path = Path(args.input)
        if not input_path.exists():
            raise SubtitlerError(f"input file not found: {input_path}")

        config = load_workflow_config(args.workflow, Path(args.config) if args.config else None)
        if args.audio_track is not None:
            config["audio"]["track"] = args.audio_track
        if args.profile:
            config["diagnostics"]["profile"] = True
        validate_workflow_config(config, workflow=args.workflow)

        env_path = Path(args.env_file)
        if not env_path.is_absolute():
            env_path = Path.cwd() / env_path
        loaded_env_keys = load_env_file(env_path)

        if config["alignment"].get("offline_model_cache", True):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        output_path = Path(args.output) if args.output else _default_output_path(input_path, args.workflow)
        sidecars_enabled = not args.no_sidecars
        sidecar_dir = (Path(args.sidecar_dir) if args.sidecar_dir else input_path.parent / "subtitle_files") if sidecars_enabled else None
        if sidecar_dir is not None:
            sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar_base = sidecar_dir / output_path.stem if sidecar_dir is not None else None
        profile_path = sidecar_base.with_suffix(".profile.csv") if sidecar_base is not None else None
        run_metadata_path = sidecar_base.with_suffix(".run.json") if sidecar_base is not None else None
        api_usage_path = sidecar_base.with_suffix(".api_usage.csv") if sidecar_base is not None else None
        aligned_text_path = sidecar_base.with_suffix(".aligned_text.txt") if sidecar_base is not None else None
        final_text_path = sidecar_base.with_suffix(".final_text.txt") if sidecar_base is not None else None
        mistranscription_path = sidecar_base.with_suffix(".possible_mistranscriptions.txt") if sidecar_base is not None else None
        regroup_profile_path = sidecar_base.with_suffix(".regroup.csv") if sidecar_base is not None else None
        llm_split_profile_path = sidecar_base.with_suffix(".llm_split.csv") if sidecar_base is not None else None
        subtitle_timing_profile_path = sidecar_base.with_suffix(".subtitle_timing.csv") if sidecar_base is not None else None
        boundary_timing_profile_path = sidecar_base.with_suffix(".boundary_timing.csv") if sidecar_base is not None else None
        chapter_markers_path = sidecar_base.with_suffix(".youtube_chapters.json") if sidecar_base is not None else None

        print(f"Input:  {input_path}")
        print(f"Output: {output_path}")
        print(f"Workflow: {args.workflow}")
        print(f"Config: {Path(args.config) if args.config else default_config_path(args.workflow)}")
        print(f"Sidecars: {sidecar_dir if sidecar_dir is not None else 'disabled'}")

        api_usage = ApiUsageLedger()
        diagnostics_enabled = sidecars_enabled and bool(config["diagnostics"]["profile"])
        profiler = PipelineProfiler(diagnostics_enabled, profile_path)

        with tempfile.TemporaryDirectory(prefix="subtitler_") as temp_name:
            temp_dir = Path(temp_name)
            wav_path = temp_dir / "input_16k_mono.wav"
            duration = get_media_duration(input_path)
            print("Extracting mono 16 kHz audio...")
            extract_audio(
                input_path,
                wav_path,
                int(config["audio"]["track"]),
                duration=duration,
                progress_callback=_progress_reporter("Audio extraction"),
            )
            samples, sample_rate = load_mono_16k_wav(wav_path)
            if duration <= 0:
                duration = len(samples) / sample_rate

            glossary_path = find_glossary(
                input_path=input_path,
                explicit=None,
                disabled=False,
                project_dir=Path(__file__).resolve().parent,
            )
            glossary = load_glossary(glossary_path)
            if glossary:
                print(f"Loaded glossary entries: {len(glossary)}")

            backend = _build_backend(config, api_usage, profiler)
            request = TranscriptionRequest(
                input_path=input_path,
                wav_path=wav_path,
                duration_sec=duration,
                sample_rate=sample_rate,
                language=config["backend"].get("language", "ja"),
                temp_dir=temp_dir,
                sidecar_base=sidecar_base,
                glossary=glossary,
                profile_enabled=diagnostics_enabled,
                workflow=args.workflow,
                metadata={
                    "samples": samples,
                    "stage_progress_reporter": _stage_progress_reporter("VAD"),
                },
            )
            backend_result = backend.transcribe(request)
            if backend_result.status == "partial" and any(
                item.code == "cost_estimate_only" for item in backend_result.diagnostics
            ):
                if run_metadata_path is not None and api_usage_path is not None:
                    _write_run_metadata(
                        run_metadata_path,
                        args,
                        config,
                        env_path,
                        loaded_env_keys,
                        backend_result,
                        api_usage,
                        api_usage_path,
                        elapsed_run_seconds=time.monotonic() - started,
                    )
                print("Cost estimate only; exiting before transcription.", flush=True)
                return 0
            aligned = backend_result_to_aligned_chunks(backend_result)
            if diagnostics_enabled and aligned_text_path is not None:
                _write_aligned_text(aligned_text_path, aligned)

            refiner = _build_refiner(config, glossary, api_usage, sidecar_base)
            try:
                chapter_markers: list[ExoMarker] = []
                mistranscription_markers: list[ExoMarker] = []
                cleanup_cfg = config["cleanup"]
                subtitle_cfg = config["subtitles"]
                subtitles = build_grouped_subtitles(
                    aligned,
                    max_chars=int(subtitle_cfg["max_chars"]),
                    min_duration=float(subtitle_cfg["min_duration"]),
                    max_duration=float(subtitle_cfg["max_duration"]),
                    gap_threshold=float(subtitle_cfg["gap_threshold"]),
                    regroup_gap_sec=float(subtitle_cfg["regroup_gap_sec"]),
                    refiner=refiner,
                    llm_splitter=refiner if cleanup_cfg.get("llm_split_planning") else None,
                    regroup_profile_path=regroup_profile_path if diagnostics_enabled else None,
                    llm_split_profile_path=llm_split_profile_path if sidecars_enabled and config["diagnostics"]["llm_split_diagnostics"] else None,
                    llm_split_console=bool(sidecars_enabled and config["diagnostics"]["llm_split_diagnostics"]),
                    subtitle_timing_profile_path=subtitle_timing_profile_path if diagnostics_enabled else None,
                    boundary_timing_profile_path=boundary_timing_profile_path if diagnostics_enabled else None,
                    chain_lead_in_sec=max(0.0, float(subtitle_cfg["chain_lead_in_sec"])),
                    cleanup_window_subtitles=int(cleanup_cfg["window_subtitles"] or _default_cleanup_window(config)),
                    cleanup_workers=int(cleanup_cfg["workers"] or _default_cleanup_workers(config)),
                    chain_split_workers=int(subtitle_cfg["chain_split_workers"] or _default_chain_split_workers(config)),
                )
                if refiner is not None:
                    if final_text_path is not None:
                        _write_final_subtitle_text(final_text_path, subtitles)
                    if args.workflow == "hosted" and config["additional_settings"]["youtube_chapters"]:
                        chapter_markers = _build_youtube_chapter_markers(
                            subtitles,
                            refiner,
                            chapter_markers_path if sidecars_enabled else None,
                        )
                    if cleanup_cfg.get("skip_final_review"):
                        print("Skipping final mistranscription check.", flush=True)
                    else:
                        print("Running final mistranscription check...", flush=True)
                        if mistranscription_path is not None:
                            mistranscription_markers = _flag_possible_mistranscriptions(
                                subtitles,
                                refiner,
                                mistranscription_path,
                            )
            finally:
                if refiner is not None:
                    refiner.close()

            profiler.write()
            if api_usage_path is not None:
                api_usage.write_csv(api_usage_path)
            _print_api_cost_summary(api_usage)
            if run_metadata_path is not None and api_usage_path is not None:
                _write_run_metadata(
                    run_metadata_path,
                    args,
                    config,
                    env_path,
                    loaded_env_keys,
                    backend_result,
                    api_usage,
                    api_usage_path,
                    elapsed_run_seconds=time.monotonic() - started,
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
    if config["backend"]["name"] == "existing-pipeline":
        return ExistingPipelineBackend(config, api_usage, profiler)
    raise SubtitlerError(f"Unknown backend: {config['backend']['name']}")


def _build_refiner(config: dict, glossary, api_usage: ApiUsageLedger, sidecar_base: Path | None):
    cleanup = config["cleanup"]
    backend = cleanup["backend"]
    if backend == "none":
        return None
    if backend == "local-llama":
        if not cleanup.get("model"):
            raise SubtitlerError("local cleanup requires cleanup.model")
        print("Starting cleanup model...")
        return LlamaServerTextRefiner(
            model_path=Path(cleanup["model"]),
            server_path=Path(cleanup["llama_server"]) if cleanup.get("llama_server") else None,
            glossary=glossary,
            mode="full",
            host="127.0.0.1",
            port=int(cleanup["server_port"]),
            ctx_size=int(cleanup["ctx_size"]),
            n_gpu_layers=int(config["backend"]["n_gpu_layers"]),
            spec_draft_model=Path(cleanup["spec_draft_model"]) if cleanup.get("spec_draft_model") else None,
            spec_draft_n_max=int(cleanup["spec_draft_n_max"]),
            log_path=(
                sidecar_base.with_suffix(".cleanup_llama.log")
                if sidecar_base is not None
                else Path(tempfile.gettempdir()) / f"subtitler_cleanup_llama_{os.getpid()}.log"
            ),
        )
    if backend == "openai":
        return OpenAITextRefiner(model=cleanup["api_model"], glossary=glossary, usage=api_usage)
    if backend == "gemini":
        return GeminiTextRefiner(model=cleanup["api_model"], glossary=glossary, usage=api_usage)
    raise SubtitlerError(f"Unknown cleanup backend: {backend}")


def _default_output_path(input_path: Path, workflow: str) -> Path:
    suffix = {
        "local": "",
        "hosted": "-hosted",
        "local-long-stream": "-long-stream-local",
        "hosted-long-stream": "-long-stream-hosted",
    }[workflow]
    return input_path.with_name(f"{input_path.stem}{suffix}.exo")


def _default_cleanup_window(config: dict) -> int:
    return 8 if config["cleanup"]["backend"] in {"gemini", "openai"} else 1


def _default_cleanup_workers(config: dict) -> int:
    return 8 if config["cleanup"]["backend"] in {"gemini", "openai"} else 1


def _default_chain_split_workers(config: dict) -> int:
    return 6 if config["backend"]["transcriber"] != "local-gemma" else 1


def _write_aligned_text(path: Path, aligned) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in sorted(aligned, key=lambda chunk: (chunk.chunk.start, chunk.chunk.end)):
        lines.append(
            f"[chunk {item.chunk.index} {item.chunk.start:.3f}-{item.chunk.end:.3f} "
            f"fallback={item.fallback} tokens={len(item.tokens)}]"
        )
        lines.append(item.text.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_final_subtitle_text(path: Path, subtitles) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{index}. {sub.text}" for index, sub in enumerate(subtitles, start=1)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _flag_possible_mistranscriptions(subtitles, refiner, output_path: Path) -> list[ExoMarker]:
    numbered_lines = [(index, sub.text) for index, sub in enumerate(subtitles, start=1)]
    flags = refiner.flag_mistranscriptions(numbered_lines)
    raw_response = getattr(refiner, "last_mistranscription_raw", "")
    by_line: dict[int, list[str]] = {}
    markers: list[ExoMarker] = []
    marked_lines: set[int] = set()
    for flag in flags:
        line_index = flag.line_number - 1
        if line_index < 0 or line_index >= len(subtitles):
            continue
        sub = subtitles[line_index]
        if flag.text not in sub.text:
            continue
        reason = flag.reason.strip() or "review candidate"
        by_line.setdefault(flag.line_number, []).append(f"{flag.text}\t{reason}")
        if flag.line_number not in marked_lines:
            markers.append(ExoMarker(sub.start_time, sub.end_time, f"{flag.line_number}: {reason}"))
            marked_lines.add(flag.line_number)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_lines = []
    for line_number in sorted(by_line):
        output_lines.extend(by_line[line_number])
    output_path.write_text("\n".join(output_lines) + ("\n" if output_lines else "NONE\n"), encoding="utf-8")
    if raw_response:
        raw_path = output_path.with_name(f"{output_path.stem}.raw.txt")
        raw_path.write_text(raw_response + "\n", encoding="utf-8")
        print(f"Wrote raw mistranscription review: {raw_path}", flush=True)
    print(f"Possible mistranscription markers: {len(markers)}", flush=True)
    print(f"Wrote possible mistranscriptions: {output_path}", flush=True)
    return markers


def _build_youtube_chapter_markers(subtitles, refiner, output_path: Path | None) -> list[ExoMarker]:
    if not subtitles:
        return []
    numbered = [
        (index, sub.start_time, sub.end_time, sub.text)
        for index, sub in enumerate(subtitles, start=1)
    ]
    try:
        chapters = refiner.suggest_chapters(numbered)
    except Exception as exc:
        print(f"Warning: YouTube chapter generation failed; continuing without chapter markers. {exc}", flush=True)
        return []

    markers: list[ExoMarker] = []
    diagnostics: list[dict[str, Any]] = []
    for chapter in chapters:
        start_index = chapter.start_subtitle_index - 1
        end_index = chapter.end_subtitle_index - 1
        if start_index < 0 or start_index >= len(subtitles) or end_index < start_index:
            continue
        end_index = min(end_index, len(subtitles) - 1)
        start_subtitle = subtitles[start_index]
        end_subtitle = subtitles[end_index]
        title = chapter.title.strip()
        if not title:
            continue
        marker = ExoMarker(start_subtitle.start_time, end_subtitle.end_time, title)
        markers.append(marker)
        diagnostics.append(
            {
                "start_subtitle_index": chapter.start_subtitle_index,
                "end_subtitle_index": chapter.end_subtitle_index,
                "start_time": marker.start_time,
                "end_time": marker.end_time,
                "title": chapter.title,
                "previous_topic": chapter.previous_topic,
                "next_topic": chapter.next_topic,
            }
        )

    if output_path is not None:
        _write_youtube_chapter_diagnostics(output_path, refiner, diagnostics)
    if markers:
        print(f"YouTube chapter markers: {len(markers)}", flush=True)
        if output_path is not None:
            print(f"Wrote YouTube chapter diagnostics: {output_path}", flush=True)
    return markers


def _write_youtube_chapter_diagnostics(output_path: Path, refiner, chapters: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": getattr(refiner, "provider", ""),
        "model": getattr(refiner, "model", ""),
        "chapters": chapters,
        "cuts": getattr(refiner, "last_youtube_chapter_cuts", []),
        "raw_response": getattr(refiner, "last_youtube_chapters_raw", ""),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_run_metadata(
    path: Path,
    args: argparse.Namespace,
    config: dict,
    env_path: Path,
    loaded_env_keys: list[str],
    backend_result,
    api_usage: ApiUsageLedger,
    api_usage_path: Path,
    elapsed_run_seconds: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    api_key_names = ["OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPGRAM_API_KEY"]
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "workflow": args.workflow,
        "config": config,
        "backend": {
            "name": backend_result.backend_name,
            "model": backend_result.model_name,
            "status": backend_result.status,
            "capabilities": backend_result.capabilities.__dict__,
            "metadata": backend_result.metadata,
            "diagnostics": [item.__dict__ for item in backend_result.diagnostics],
        },
        "actual_api_cost_usd": api_usage.total_cost_usd,
        "actual_api_total_tokens": api_usage.total_tokens,
        "api_usage_path": str(api_usage_path),
        "api_usage_by_provider_model": api_usage.by_provider_model(),
        "elapsed_run_seconds": elapsed_run_seconds,
        "elapsed_run_display": _format_elapsed(elapsed_run_seconds),
        "env_file": str(env_path),
        "env_file_loaded": bool(loaded_env_keys),
        "env_keys_loaded": sorted(loaded_env_keys),
        "api_keys_present": {name: bool(os.environ.get(name)) for name in api_key_names},
        "argv": sys.argv[1:],
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _progress_reporter(label: str, step: int = 10):
    next_percent = {"value": step}

    def report(progress: float) -> None:
        while progress + 1e-9 >= next_percent["value"] and next_percent["value"] <= 100:
            print(f"{label} progress: {next_percent['value']}%", flush=True)
            next_percent["value"] += step

    return report


def _stage_progress_reporter(label: str, step: int = 10):
    reporters: dict[str, object] = {}

    def report(stage: str, progress: float) -> None:
        if stage not in reporters:
            reporters[stage] = _progress_reporter(f"{label} {stage}", step)
        reporters[stage](progress)

    return report


def _print_api_cost_summary(api_usage: ApiUsageLedger) -> None:
    if not api_usage.rows:
        return
    print("Hosted API cost summary:", flush=True)
    for provider, cost in sorted(api_usage.total_cost_by_provider().items()):
        print(f"  {provider}: ${cost:.4f}", flush=True)
    print(f"  total: ${api_usage.total_cost_usd:.4f}", flush=True)


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


if __name__ == "__main__":
    raise SystemExit(main())
