#!/usr/bin/env python3
"""Offline Gemma transcription to AviUtl EXO subtitles."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from subtitler.aligner import ctc_language_code, is_japanese_language
from subtitler.alignment_pool import AlignmentConfig, AlignmentPool
from subtitler.api_costs import estimate_run_cost
from subtitler.api_usage import ApiUsageLedger
from subtitler.audio import extract_audio, get_media_duration, load_mono_16k_wav
from subtitler.env import load_env_file
from subtitler.errors import SubtitlerError
from subtitler.exo import generate_exo_file, write_exo
from subtitler.external_refiners import GeminiTextRefiner, OpenAITextRefiner
from subtitler.external_transcribers import GeminiTranscriber, OpenAITranscriber, require_api_key, verify_gemini_model_available, verify_openai_model_available
from subtitler.glossary import find_glossary, load_glossary
from subtitler.models import ExoMarker, ExoSettings
from subtitler.profiling import PipelineProfiler, now
from subtitler.subtitle_planner import build_grouped_subtitles
from subtitler.text_refiner import LlamaServerTextRefiner
from subtitler.transcriber import ServerGemmaTranscriber
from subtitler.vad import segment_speech


def default_align_workers() -> int:
    return max(1, (os.cpu_count() or 4) // 4)


def _effective_cleanup_backend(args: argparse.Namespace) -> str:
    if args.cleanup_backend == "none" and args.cleanup_model:
        return "local-llama"
    return args.cleanup_backend


def _transcription_model(args: argparse.Namespace) -> str:
    if args.transcriber_backend == "local-gemma":
        return args.transcription_model or args.model or ""
    return args.transcription_model or ""


def _uses_hosted_api(transcriber_backend: str, cleanup_backend: str) -> bool:
    return transcriber_backend in {"gemini", "openai"} or cleanup_backend in {"gemini", "openai"}


def _tuning_profile(args: argparse.Namespace, cleanup_backend: str) -> str:
    if args.tuning_profile != "auto":
        return args.tuning_profile
    return "hosted" if _uses_hosted_api(args.transcriber_backend, cleanup_backend) else "local"


def _cleanup_window_subtitles(args: argparse.Namespace, cleanup_backend: str) -> int:
    if args.cleanup_window_subtitles is not None:
        return max(1, args.cleanup_window_subtitles)
    return 8 if _tuning_profile(args, cleanup_backend) == "hosted" and cleanup_backend != "none" else 1


def _transcription_workers(args: argparse.Namespace, cleanup_backend: str) -> int:
    if args.transcription_workers is not None:
        return max(1, args.transcription_workers)
    return 4 if _tuning_profile(args, cleanup_backend) == "hosted" and args.transcriber_backend != "local-gemma" else 1


def _chain_split_workers(args: argparse.Namespace, cleanup_backend: str) -> int:
    if args.chain_split_workers is not None:
        return max(1, args.chain_split_workers)
    return 6 if _tuning_profile(args, cleanup_backend) == "hosted" else 1


def _cleanup_workers(args: argparse.Namespace, cleanup_backend: str) -> int:
    if args.cleanup_workers is not None:
        return max(1, args.cleanup_workers)
    return 8 if _tuning_profile(args, cleanup_backend) == "hosted" and cleanup_backend in {"gemini", "openai"} else 1


def _preflight_hosted_models(
    *,
    transcriber_backend: str,
    transcription_model: str,
    cleanup_backend: str,
    cleanup_model: str,
) -> None:
    if transcriber_backend == "gemini":
        verify_gemini_model_available(transcription_model, require_api_key("GEMINI_API_KEY"))
    elif transcriber_backend == "openai":
        verify_openai_model_available(transcription_model, require_api_key("OPENAI_API_KEY"))

    if cleanup_backend == "gemini":
        verify_gemini_model_available(cleanup_model, require_api_key("GEMINI_API_KEY"))
    elif cleanup_backend == "openai":
        verify_openai_model_available(cleanup_model, require_api_key("OPENAI_API_KEY"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate AviUtl .exo subtitles using offline Gemma, Silero VAD, and CTC alignment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Input audio or video file")
    parser.add_argument("-o", "--output", help="Output .exo file")
    parser.add_argument("--model", help="Local Gemma GGUF model path")
    parser.add_argument(
        "--transcriber-backend",
        choices=["local-gemma", "gemini", "openai"],
        default="local-gemma",
        help="Transcription backend",
    )
    parser.add_argument("--transcription-model", help="Hosted transcription model, or local model path override")
    parser.add_argument("--mmproj", help="Optional multimodal/projector file path")
    parser.add_argument(
        "--audio-track",
        type=int,
        default=1,
        help="Audio stream index, 0-based. Default is 1, the second audio track.",
    )
    parser.add_argument("--language", default="ja", help="Transcription/alignment language")
    parser.add_argument("--temp-dir", help="Temporary directory")
    parser.add_argument("--keep-temp", action="store_true", help="Keep chunk WAV files")
    parser.add_argument("--profile", action="store_true", help="Write per-chunk pipeline timing CSV")
    parser.add_argument("--env-file", default=".env", help="Load API keys/settings from this dotenv-style file")
    parser.add_argument("--estimate-cost-only", action="store_true", help="Estimate hosted API cost after VAD and exit")
    parser.add_argument("--max-estimated-api-cost-usd", type=float, default=5.0, help="Abort hosted API runs above this estimate")
    parser.add_argument("--allow-api-spend", action="store_true", help="Allow hosted API runs above the estimate guard")
    parser.add_argument(
        "--tuning-profile",
        choices=["auto", "local", "hosted"],
        default="auto",
        help="Default tuning profile for model-sensitive settings",
    )
    parser.add_argument("--llama-server", help="Path to llama-server.exe for server backend")
    parser.add_argument("--server-port", type=int, default=8081, help="llama-server port for server backend")

    model_group = parser.add_argument_group("Gemma / llama.cpp options")
    model_group.add_argument("--n-gpu-layers", type=int, default=-1)
    model_group.add_argument("--ctx-size", type=int, default=8192)
    model_group.add_argument("--audio-prep-workers", type=int, default=2)
    model_group.add_argument(
        "--transcription-workers",
        type=int,
        help="Concurrent hosted transcription requests. Defaults to 1 local, 4 hosted.",
    )
    model_group.add_argument(
        "--transcription-max-split-depth",
        type=int,
        default=2,
        help="Recursive VAD re-split attempts when server transcription output looks incomplete or contaminated",
    )
    model_group.add_argument("--align-workers", type=int, default=default_align_workers(), help="Defaults to CPU count / 4")
    model_group.add_argument("--align-torch-threads", type=int, help="PyTorch CPU threads per aligner worker")
    model_group.add_argument("--align-emission-batch-size", type=int, default=4, help="CTC emission batch size")

    glossary_group = parser.add_argument_group("Glossary options")
    glossary_group.add_argument("--glossary", help="Plain-text glossary path")
    glossary_group.add_argument("--no-glossary", action="store_true", help="Disable glossary auto-discovery")

    vad_group = parser.add_argument_group("VAD options")
    vad_group.add_argument("--max-chunk-sec", type=float, default=30.0)
    vad_group.add_argument("--min-speech-sec", type=float, default=0.25)
    vad_group.add_argument("--min-silence-ms", type=int, default=400)
    vad_group.add_argument("--speech-pad-ms", type=int, default=200)

    align_group = parser.add_argument_group("Alignment options")
    align_group.add_argument(
        "--alignment-model",
        default="MahmoudAshraf/mms-300m-1130-forced-aligner",
    )
    align_group.add_argument("--alignment-device", default="auto")
    align_group.add_argument(
        "--alignment-max-split-depth",
        type=int,
        default=4,
        help="Recursive VAD re-split attempts when a chunk is too dense for CTC alignment",
    )
    align_group.add_argument(
        "--offline-model-cache",
        action="store_true",
        help="Use local Hugging Face/Transformers cache only for alignment models",
    )

    sub_group = parser.add_argument_group("Subtitle shaping options")
    sub_group.add_argument("--max-chars", type=int, default=40)
    sub_group.add_argument("--min-duration", type=float, default=0.40)
    sub_group.add_argument("--max-duration", type=float, default=6.0)
    sub_group.add_argument("--gap-threshold", type=float, default=0.25)
    sub_group.add_argument("--regroup-gap-sec", type=float, default=0.5)
    sub_group.add_argument("--llm-split-planning", choices=["off", "cleanup-model"], default="off")
    sub_group.add_argument("--llm-split-diagnostics", action="store_true")
    sub_group.add_argument(
        "--chain-split-workers",
        type=int,
        help="Concurrent chain splitting workers. Defaults to 1 local, 6 hosted.",
    )
    sub_group.add_argument("--chain-lead-in-sec", type=float, default=0.08)

    cleanup_group = parser.add_argument_group("Cleanup LLM options")
    cleanup_group.add_argument(
        "--cleanup-backend",
        choices=["none", "local-llama", "gemini", "openai"],
        default="none",
        help="Cleanup/refinement backend",
    )
    cleanup_group.add_argument("--cleanup-model", help="Local GGUF text model for subtitle cleanup")
    cleanup_group.add_argument("--cleanup-api-model", help="Hosted cleanup/refinement model")
    cleanup_group.add_argument(
        "--cleanup-window-subtitles",
        type=int,
        help="Subtitle lines per cleanup request. Defaults to 1 for local models and 8 for hosted models.",
    )
    cleanup_group.add_argument(
        "--cleanup-workers",
        type=int,
        help="Concurrent hosted cleanup requests. Defaults to 1 local, 8 hosted.",
    )
    cleanup_group.add_argument(
        "--skip-final-review",
        action="store_true",
        help="Skip final possible-mistranscription review and layer-4 QA markers",
    )
    cleanup_group.add_argument("--cleanup-llama-server", help="Path to llama-server.exe for cleanup backend")
    cleanup_group.add_argument("--cleanup-server-port", type=int, default=8082)
    cleanup_group.add_argument("--cleanup-ctx-size", type=int, default=4096)

    exo_group = parser.add_argument_group("EXO options")
    exo_group.add_argument("--width", type=int, default=2560)
    exo_group.add_argument("--height", type=int, default=1440)
    exo_group.add_argument("--fps", type=int, default=60)
    exo_group.add_argument("--font", default="M+ 2p heavy")
    exo_group.add_argument("--font-size", type=int, default=60)
    exo_group.add_argument("--y-position", type=float, default=717.0)
    exo_group.add_argument("--sidecar-dir", help="Directory for diagnostics/intermediate subtitle files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.align_torch_threads is None:
        cpu_count = os.cpu_count() or 4
        args.align_torch_threads = max(1, cpu_count // max(1, args.align_workers))
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else input_path.with_suffix(".exo")
    temp_root = Path(args.temp_dir) if args.temp_dir else None
    sidecar_dir = Path(args.sidecar_dir) if args.sidecar_dir else input_path.parent / "subtitle_files"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar_base = sidecar_dir / output_path.stem
    profile_path = sidecar_base.with_suffix(".profile.csv")
    regroup_profile_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.regroup.csv")
    llm_split_profile_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.llm_split.csv")
    subtitle_timing_profile_path = profile_path.with_name(
        f"{profile_path.stem.removesuffix('.profile')}.subtitle_timing.csv"
    )
    boundary_timing_profile_path = profile_path.with_name(
        f"{profile_path.stem.removesuffix('.profile')}.boundary_timing.csv"
    )
    run_metadata_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.run.json")
    api_usage_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.api_usage.csv")
    aligned_text_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.aligned_text.txt")
    final_text_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.final_text.txt")
    mistranscription_path = profile_path.with_name(
        f"{profile_path.stem.removesuffix('.profile')}.possible_mistranscriptions.txt"
    )

    try:
        env_path = Path(args.env_file)
        if not env_path.is_absolute():
            env_path = Path.cwd() / env_path
        loaded_env_keys = load_env_file(env_path)
        cleanup_backend = _effective_cleanup_backend(args)
        transcription_model = _transcription_model(args)
        cleanup_api_model = args.cleanup_api_model or ""
        cleanup_window_subtitles = _cleanup_window_subtitles(args, cleanup_backend)
        transcription_workers = _transcription_workers(args, cleanup_backend)
        chain_split_workers = _chain_split_workers(args, cleanup_backend)
        cleanup_workers = _cleanup_workers(args, cleanup_backend)
        if args.transcriber_backend != "local-gemma" and not transcription_model:
            raise SubtitlerError("--transcription-model is required for hosted transcription")
        if cleanup_backend == "local-llama" and not args.cleanup_model:
            raise SubtitlerError("--cleanup-model is required when --cleanup-backend local-llama")
        if cleanup_backend in {"gemini", "openai"} and not cleanup_api_model:
            raise SubtitlerError("--cleanup-api-model is required for hosted cleanup")
        api_usage = ApiUsageLedger()

        if args.offline_model_cache:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        with tempfile.TemporaryDirectory(dir=temp_root, prefix="subtitler_") as temp_name:
            temp_dir = Path(temp_name)
            wav_path = temp_dir / "input_16k_mono.wav"

            print(f"Input:  {input_path}")
            print(f"Output: {output_path}")
            print(f"Sidecars: {sidecar_dir}")
            print("Extracting mono 16 kHz audio...")
            extract_audio(input_path, wav_path, args.audio_track)
            duration = get_media_duration(input_path)
            samples, sample_rate = load_mono_16k_wav(wav_path)
            if duration <= 0:
                duration = len(samples) / sample_rate

            print("Running Silero VAD...")
            chunks = segment_speech(
                samples=samples,
                sample_rate=sample_rate,
                max_chunk_sec=args.max_chunk_sec,
                min_speech_sec=args.min_speech_sec,
                min_silence_ms=args.min_silence_ms,
                speech_pad_ms=args.speech_pad_ms,
                temp_dir=temp_dir,
                keep_temp=True,
            )
            print(f"VAD chunks: {len(chunks)}")
            speech_seconds = sum(max(0.0, chunk.end - chunk.start) for chunk in chunks)
            estimated_api_cost = estimate_run_cost(
                transcriber_backend=args.transcriber_backend,
                transcription_model=transcription_model,
                cleanup_backend=cleanup_backend,
                cleanup_model=cleanup_api_model,
                speech_seconds=speech_seconds,
            )
            print(
                "Estimated hosted API cost: "
                f"${estimated_api_cost:.4f} "
                f"(speech={speech_seconds / 60.0:.2f} min, "
                f"transcriber={args.transcriber_backend}:{transcription_model or 'local'}, "
                f"cleanup={cleanup_backend}:{cleanup_api_model or args.cleanup_model or 'none'})",
                flush=True,
            )
            if args.profile:
                _write_run_metadata(
                    run_metadata_path,
                    args,
                    env_path,
                    loaded_env_keys,
                    estimated_api_cost=estimated_api_cost,
                    api_usage=api_usage,
                    api_usage_path=api_usage_path,
                )
            if args.estimate_cost_only:
                print("Cost estimate only; exiting before transcription.", flush=True)
                return 0
            if _uses_hosted_api(args.transcriber_backend, cleanup_backend):
                if estimated_api_cost > args.max_estimated_api_cost_usd and not args.allow_api_spend:
                    print(
                        "Error: estimated hosted API cost "
                        f"${estimated_api_cost:.4f} exceeds limit "
                        f"${args.max_estimated_api_cost_usd:.2f}. "
                        "Re-run with --allow-api-spend to proceed.",
                        file=sys.stderr,
                    )
                    return 1
                _preflight_hosted_models(
                    transcriber_backend=args.transcriber_backend,
                    transcription_model=transcription_model,
                    cleanup_backend=cleanup_backend,
                    cleanup_model=cleanup_api_model,
                )
            glossary_path = find_glossary(
                input_path=input_path,
                explicit=Path(args.glossary) if args.glossary else None,
                disabled=args.no_glossary,
                project_dir=Path(__file__).resolve().parent,
            )
            glossary = load_glossary(glossary_path)
            if glossary:
                print(f"Loaded glossary entries: {len(glossary)}")
            profiler = PipelineProfiler(enabled=args.profile, output_path=profile_path)
            for chunk in chunks:
                profiler.start_chunk(chunk.index, chunk.start, chunk.end)

            transcriber = _build_transcriber(args, temp_dir, glossary, api_usage)

            try:
                split_size = "char" if is_japanese_language(args.language) else "word"
                ctc_language = ctc_language_code(args.language)
                print(
                    "Alignment: "
                    f"model={args.alignment_model}, language={args.language}, "
                    f"ctc_language={ctc_language}, split_size={split_size}, "
                    "star_frequency=edges",
                    flush=True,
                )
                alignment_config = AlignmentConfig(
                    model_name=args.alignment_model,
                    language=args.language,
                    device=args.alignment_device,
                    split_size=split_size,
                    temp_dir=temp_dir,
                    sample_rate=sample_rate,
                    emission_batch_size=args.align_emission_batch_size,
                    torch_threads=args.align_torch_threads,
                    max_split_depth=max(0, args.alignment_max_split_depth),
                )

                aligned = _transcribe_and_align(
                    chunks=chunks,
                    transcriber=transcriber,
                    alignment_config=alignment_config,
                    profiler=profiler,
                    audio_prep_workers=max(1, args.audio_prep_workers),
                    align_workers=max(1, args.align_workers),
                    transcription_workers=transcription_workers,
                )
                if args.profile:
                    _write_aligned_text(aligned_text_path, aligned)
            finally:
                close = getattr(transcriber, "close", None)
                if close is not None:
                    close()

            refiner = None
            if cleanup_backend == "local-llama":
                print("Starting cleanup model...")
                refiner = LlamaServerTextRefiner(
                    model_path=Path(args.cleanup_model),
                    server_path=Path(args.cleanup_llama_server) if args.cleanup_llama_server else None,
                    glossary=glossary,
                    mode="full",
                    host="127.0.0.1",
                    port=args.cleanup_server_port,
                    ctx_size=args.cleanup_ctx_size,
                    n_gpu_layers=args.n_gpu_layers,
                )
            elif cleanup_backend == "openai":
                refiner = OpenAITextRefiner(model=cleanup_api_model, glossary=glossary, usage=api_usage)
            elif cleanup_backend == "gemini":
                refiner = GeminiTextRefiner(model=cleanup_api_model, glossary=glossary, usage=api_usage)
            try:
                chain_markers: list[ExoMarker] = []
                mistranscription_markers: list[ExoMarker] = []
                subtitles = build_grouped_subtitles(
                    aligned,
                    max_chars=args.max_chars,
                    min_duration=args.min_duration,
                    max_duration=args.max_duration,
                    gap_threshold=args.gap_threshold,
                    regroup_gap_sec=args.regroup_gap_sec,
                    refiner=refiner,
                    llm_splitter=refiner if args.llm_split_planning == "cleanup-model" else None,
                    regroup_profile_path=regroup_profile_path if args.profile else None,
                    llm_split_profile_path=llm_split_profile_path if args.llm_split_diagnostics else None,
                    llm_split_console=args.llm_split_diagnostics,
                    chain_markers=chain_markers,
                    subtitle_timing_profile_path=subtitle_timing_profile_path if args.profile else None,
                    boundary_timing_profile_path=boundary_timing_profile_path if args.profile else None,
                    chain_lead_in_sec=max(0.0, args.chain_lead_in_sec),
                    cleanup_window_subtitles=cleanup_window_subtitles,
                    cleanup_workers=cleanup_workers,
                    chain_split_workers=chain_split_workers,
                )
                if refiner is not None:
                    _write_final_subtitle_text(final_text_path, subtitles)
                    if args.skip_final_review:
                        print("Skipping final mistranscription check.", flush=True)
                    else:
                        print("Running final mistranscription check...", flush=True)
                        mistranscription_markers = _flag_possible_mistranscriptions(
                            subtitles,
                            refiner,
                            mistranscription_path,
                        )
            finally:
                if refiner is not None:
                    refiner.close()
            profiler.write()
            api_usage.write_csv(api_usage_path)
            _print_api_cost_summary(api_usage)
            if args.profile:
                print(f"Wrote profile: {profile_path}")
                print(f"Wrote run metadata: {run_metadata_path}")
                print(f"Wrote API usage: {api_usage_path}")
                _write_run_metadata(
                    run_metadata_path,
                    args,
                    env_path,
                    loaded_env_keys,
                    estimated_api_cost=estimated_api_cost,
                    api_usage=api_usage,
                    api_usage_path=api_usage_path,
                )
            settings = ExoSettings(
                width=args.width,
                height=args.height,
                rate=args.fps,
                font=args.font,
                font_size=args.font_size,
                y_position=args.y_position,
            )
            content = generate_exo_file(
                subtitles,
                settings,
                duration,
                insert_initial_empty=True,
                vad_markers=_build_vad_markers(chunks),
                chain_markers=chain_markers,
                mistranscription_markers=mistranscription_markers,
            )
            write_exo(output_path, content)

            if args.keep_temp:
                keep_dir = output_path.with_suffix(".chunks")
                keep_dir.mkdir(exist_ok=True)
                for path in temp_dir.glob("*.wav"):
                    target = keep_dir / path.name
                    target.write_bytes(path.read_bytes())
                print(f"Kept temporary WAV files in: {keep_dir}")

            print(f"Successfully generated: {output_path}")
            print(f"Total subtitles: {len(subtitles)}")
            return 0
    except SubtitlerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


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


def _build_vad_markers(chunks) -> list[ExoMarker]:
    return [ExoMarker(chunk.start, chunk.end, f"VAD {index}") for index, chunk in enumerate(chunks, start=1)]


def _build_transcriber(args: argparse.Namespace, temp_dir: Path, glossary, api_usage: ApiUsageLedger):
    transcription_model = _transcription_model(args)
    if args.transcriber_backend == "local-gemma":
        if not transcription_model:
            raise SubtitlerError("--model or --transcription-model is required for local Gemma transcription")
        if not args.mmproj:
            raise SubtitlerError("--mmproj is required for Gemma audio transcription")
        return ServerGemmaTranscriber(
            model_path=Path(transcription_model),
            mmproj=Path(args.mmproj),
            n_gpu_layers=args.n_gpu_layers,
            ctx_size=args.ctx_size,
            temp_dir=temp_dir,
            server_path=Path(args.llama_server) if args.llama_server else None,
            host="127.0.0.1",
            port=args.server_port,
            glossary=glossary,
            max_transcription_split_depth=max(0, args.transcription_max_split_depth),
        )
    if not transcription_model:
        raise SubtitlerError("--transcription-model is required for hosted transcription")
    if args.transcriber_backend == "gemini":
        return GeminiTranscriber(
            model=transcription_model,
            temp_dir=temp_dir,
            usage=api_usage,
            glossary=glossary,
            max_transcription_split_depth=max(0, args.transcription_max_split_depth),
        )
    if args.transcriber_backend == "openai":
        return OpenAITranscriber(
            model=transcription_model,
            temp_dir=temp_dir,
            usage=api_usage,
            glossary=glossary,
            language=args.language,
            max_transcription_split_depth=max(0, args.transcription_max_split_depth),
        )
    raise SubtitlerError(f"Unknown transcription backend: {args.transcriber_backend}")


def _write_run_metadata(
    path: Path,
    args: argparse.Namespace,
    env_path: Path,
    loaded_env_keys: list[str],
    *,
    estimated_api_cost: float = 0.0,
    api_usage: ApiUsageLedger | None = None,
    api_usage_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    api_key_names = ["OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPGRAM_API_KEY"]
    args_dict = vars(args).copy()
    cleanup_backend = _effective_cleanup_backend(args)
    transcription_model = _transcription_model(args)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "transcriber_backend": args.transcriber_backend,
        "transcriber_model": transcription_model,
        "cleanup_backend": cleanup_backend,
        "cleanup_model": args.cleanup_api_model if cleanup_backend in {"gemini", "openai"} else args.cleanup_model or "",
        "tuning_profile": _tuning_profile(args, cleanup_backend),
        "cleanup_window_subtitles": _cleanup_window_subtitles(args, cleanup_backend),
        "transcription_workers": _transcription_workers(args, cleanup_backend),
        "chain_split_workers": _chain_split_workers(args, cleanup_backend),
        "cleanup_workers": _cleanup_workers(args, cleanup_backend),
        "skip_final_review": bool(args.skip_final_review),
        "alignment_model": args.alignment_model,
        "alignment_device": args.alignment_device,
        "language": args.language,
        "estimated_api_cost_usd": estimated_api_cost,
        "actual_api_cost_usd": api_usage.total_cost_usd if api_usage is not None else 0.0,
        "actual_api_total_tokens": api_usage.total_tokens if api_usage is not None else 0,
        "api_usage_path": str(api_usage_path) if api_usage_path is not None else "",
        "api_usage_by_provider_model": api_usage.by_provider_model() if api_usage is not None else [],
        "env_file": str(env_path),
        "env_file_loaded": bool(loaded_env_keys),
        "env_keys_loaded": sorted(loaded_env_keys),
        "api_keys_present": {name: bool(os.environ.get(name)) for name in api_key_names},
        "argv": sys.argv[1:],
        "args": args_dict,
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _print_api_cost_summary(api_usage: ApiUsageLedger) -> None:
    if not api_usage.rows:
        return
    print("Hosted API cost summary:", flush=True)
    for provider, cost in sorted(api_usage.total_cost_by_provider().items()):
        print(f"  {provider}: ${cost:.4f}", flush=True)
    print(f"  total: ${api_usage.total_cost_usd:.4f}", flush=True)
    operation_costs = api_usage.total_cost_by_operation()
    if operation_costs:
        print("Hosted API cost by operation:", flush=True)
        for operation, cost in sorted(operation_costs.items()):
            print(f"  {operation}: ${cost:.4f}", flush=True)


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
        for text in by_line[line_number]:
            output_lines.append(f"{line_number}\t{text}")
    output_path.write_text("\n".join(output_lines) + ("\n" if output_lines else "NONE\n"), encoding="utf-8")
    if raw_response:
        raw_path = output_path.with_name(f"{output_path.stem}.raw.txt")
        raw_path.write_text(raw_response + "\n", encoding="utf-8")
        print(f"Wrote raw mistranscription review: {raw_path}", flush=True)
    print(f"Possible mistranscription markers: {len(markers)}", flush=True)
    print(f"Wrote possible mistranscriptions: {output_path}", flush=True)
    return markers


def _transcribe_and_align(
    chunks,
    transcriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    audio_prep_workers: int,
    align_workers: int,
    transcription_workers: int = 1,
):
    if hasattr(transcriber, "prepare_payload") and hasattr(transcriber, "transcribe_payload"):
        return _transcribe_and_align_server(
            chunks, transcriber, alignment_config, profiler, audio_prep_workers, align_workers
        )

    if transcription_workers > 1:
        return _transcribe_and_align_parallel(
            chunks,
            transcriber,
            alignment_config,
            profiler,
            transcription_workers,
            align_workers,
        )

    pool = AlignmentPool(align_workers, alignment_config, profiler)
    try:
        for i, chunk in enumerate(chunks, start=1):
            print(f"Transcribing chunk {i}/{len(chunks)} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
            start = now()
            transcript = transcriber.transcribe(chunk)
            profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
            if not transcript.text:
                print(f"Warning: empty transcript for chunk {chunk.index}")
                continue
            pool.submit(transcript)
        print("Waiting for alignment workers...", flush=True)
        return pool.close_and_collect()
    except Exception:
        raise


def _transcribe_one(transcriber, chunk, profiler: PipelineProfiler):
    start = now()
    transcript = transcriber.transcribe(chunk)
    profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
    return transcript


def _transcribe_and_align_parallel(
    chunks,
    transcriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    transcription_workers: int,
    align_workers: int,
):
    pool = AlignmentPool(align_workers, alignment_config, profiler)
    try:
        with ThreadPoolExecutor(max_workers=max(1, transcription_workers)) as transcribe_pool:
            futures = {}
            for i, chunk in enumerate(chunks, start=1):
                print(f"Queueing transcription chunk {i}/{len(chunks)} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
                futures[transcribe_pool.submit(_transcribe_one, transcriber, chunk, profiler)] = (i, chunk)
            for future in as_completed(futures):
                i, chunk = futures[future]
                print(f"Transcription complete: {i}/{len(chunks)} [{chunk.start:.2f}-{chunk.end:.2f}s]", flush=True)
                try:
                    transcript = future.result()
                    if not transcript.text:
                        print(f"Warning: empty transcript for chunk {chunk.index}")
                        continue
                    pool.submit(transcript)
                except Exception as exc:
                    profiler.mark_error(chunk.index, exc)
                    raise
        print("Waiting for alignment workers...", flush=True)
        return pool.close_and_collect()
    except Exception:
        raise


def _prepare_payload(transcriber, chunk, profiler: PipelineProfiler):
    start = now()
    payload = transcriber.prepare_payload(chunk)
    profiler.add_ms(chunk.index, "payload_prepare_ms", (now() - start) * 1000)
    return payload


def _transcribe_and_align_server(
    chunks,
    transcriber,
    alignment_config: AlignmentConfig,
    profiler: PipelineProfiler,
    audio_prep_workers: int,
    align_workers: int,
):
    from subtitler.models import TranscriptChunk

    prep_futures: dict[int, Future] = {}
    next_to_submit = 0
    total = len(chunks)
    pool = AlignmentPool(align_workers, alignment_config, profiler)
    with ThreadPoolExecutor(max_workers=audio_prep_workers) as prep_pool:
        while next_to_submit < min(audio_prep_workers, total):
            chunk = chunks[next_to_submit]
            prep_futures[chunk.index] = prep_pool.submit(_prepare_payload, transcriber, chunk, profiler)
            next_to_submit += 1

        for i, chunk in enumerate(chunks, start=1):
            if chunk.index not in prep_futures:
                prep_futures[chunk.index] = prep_pool.submit(_prepare_payload, transcriber, chunk, profiler)
            print(f"Transcribing chunk {i}/{total} [{chunk.start:.2f}-{chunk.end:.2f}s]...")
            try:
                payload = prep_futures.pop(chunk.index).result()
                while next_to_submit < total and len(prep_futures) < audio_prep_workers:
                    upcoming = chunks[next_to_submit]
                    prep_futures[upcoming.index] = prep_pool.submit(_prepare_payload, transcriber, upcoming, profiler)
                    next_to_submit += 1
                start = now()
                text = transcriber.transcribe_payload(chunk, payload)
                profiler.add_ms(chunk.index, "transcribe_wait_ms", (now() - start) * 1000)
                if not text:
                    print(f"Warning: empty transcript for chunk {chunk.index}")
                    continue
                transcript = TranscriptChunk(chunk=chunk, text=text)
                pool.submit(transcript)
            except Exception as exc:
                profiler.mark_error(chunk.index, exc)
                raise

    print("Waiting for alignment workers...", flush=True)
    return pool.close_and_collect()


if __name__ == "__main__":
    raise SystemExit(main())
