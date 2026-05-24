#!/usr/bin/env python3
"""Offline Gemma transcription to AviUtl EXO subtitles."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from subtitler.alignment_pool import AlignmentConfig, AlignmentPool
from subtitler.audio import extract_audio, get_media_duration, load_mono_16k_wav
from subtitler.errors import SubtitlerError
from subtitler.exo import generate_exo_file, write_exo
from subtitler.glossary import find_glossary, load_glossary
from subtitler.models import ExoMarker, ExoSettings
from subtitler.profiling import PipelineProfiler, now
from subtitler.subtitle_planner import build_grouped_subtitles
from subtitler.text_refiner import LlamaServerTextRefiner
from subtitler.transcriber import GemmaTranscriber, NativeGemmaTranscriber, ServerGemmaTranscriber
from subtitler.vad import segment_speech


def default_align_workers() -> int:
    return max(1, (os.cpu_count() or 4) // 4)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate AviUtl .exo subtitles using offline Gemma, Silero VAD, and CTC alignment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Input audio or video file")
    parser.add_argument("-o", "--output", help="Output .exo file")
    parser.add_argument("--model", required=True, help="Local Gemma GGUF model path")
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
    parser.add_argument("--verbose", action="store_true", help="Show verbose llama.cpp/model internals")
    parser.add_argument("--profile", action="store_true", help="Write per-chunk pipeline timing CSV")
    parser.add_argument("--profile-output", help="Output path for --profile CSV")
    parser.add_argument(
        "--transcriber-backend",
        choices=["native", "python", "server"],
        default="native",
        help="Use native llama-mtmd-cli, managed llama-server, or llama-cpp-python chat calls for Gemma audio",
    )
    parser.add_argument("--llama-mtmd-cli", help="Path to llama-mtmd-cli.exe for native backend")
    parser.add_argument("--llama-server", help="Path to llama-server.exe for server backend")
    parser.add_argument("--server-host", default="127.0.0.1", help="llama-server host for server backend")
    parser.add_argument("--server-port", type=int, default=8081, help="llama-server port for server backend")

    model_group = parser.add_argument_group("Gemma / llama.cpp options")
    model_group.add_argument("--n-gpu-layers", type=int, default=-1)
    model_group.add_argument("--ctx-size", type=int, default=8192)
    model_group.add_argument("--threads", type=int, default=0)
    model_group.add_argument("--batch-size", type=int)
    model_group.add_argument("--audio-prep-workers", type=int, default=2)
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
        "--alignment-split-size",
        default=None,
        choices=["word", "char"],
        help="Defaults to char for Japanese, word otherwise",
    )
    align_group.add_argument(
        "--alignment-star-frequency",
        choices=["edges", "segment"],
        default="edges",
        help="Where to insert CTC wildcard tokens during forced alignment",
    )
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
    sub_group.add_argument("--max-lines", type=int, default=2)
    sub_group.add_argument("--min-duration", type=float, default=0.40)
    sub_group.add_argument("--max-duration", type=float, default=6.0)
    sub_group.add_argument("--gap-threshold", type=float, default=0.25)
    sub_group.add_argument("--regroup-adjacent", dest="regroup_adjacent", action="store_true", default=True)
    sub_group.add_argument("--no-regroup-adjacent", dest="regroup_adjacent", action="store_false")
    sub_group.add_argument("--regroup-gap-sec", type=float, default=0.5)
    sub_group.add_argument("--regroup-max-window-sec", type=float, default=18.0)
    sub_group.add_argument("--regroup-max-window-chars", type=int, default=220)
    sub_group.add_argument("--llm-split-planning", choices=["off", "cleanup-model"], default="off")
    sub_group.add_argument("--llm-split-diagnostics", action="store_true")
    sub_group.add_argument("--llm-split-max-input-chars", type=int, default=240)
    sub_group.add_argument("--llm-split-second-pass-max-input-chars", type=int, default=240)
    sub_group.add_argument("--chain-lead-in-sec", type=float, default=0.08)
    sub_group.add_argument("--chain-lead-in-growth-sec", type=float, default=0.0)
    sub_group.add_argument("--chain-lead-in-max-sec", type=float, default=0.20)
    sub_group.add_argument("--regroup-ramp-start-sec", type=float, default=0.2)
    sub_group.add_argument("--regroup-ramp-step-sec", type=float, default=0.1)
    sub_group.add_argument("--regroup-ramp-max-chain-sec", type=float, default=120.0)
    sub_group.add_argument("--regroup-ramp-max-chain-tokens", type=int, default=900)

    cleanup_group = parser.add_argument_group("Cleanup LLM options")
    cleanup_group.add_argument("--cleanup-model", help="Local GGUF text model for subtitle cleanup")
    cleanup_group.add_argument("--cleanup-llama-server", help="Path to llama-server.exe for cleanup backend")
    cleanup_group.add_argument("--cleanup-server-host", default="127.0.0.1")
    cleanup_group.add_argument("--cleanup-server-port", type=int, default=8082)
    cleanup_group.add_argument("--cleanup-ctx-size", type=int, default=4096)
    cleanup_group.add_argument("--cleanup-n-gpu-layers", type=int)
    cleanup_group.add_argument("--cleanup-mode", choices=["off", "fillers", "glossary", "full"], default="off")
    cleanup_group.add_argument("--cleanup-window-subtitles", type=int, default=1)

    exo_group = parser.add_argument_group("EXO options")
    exo_group.add_argument("--width", type=int, default=2560)
    exo_group.add_argument("--height", type=int, default=1440)
    exo_group.add_argument("--fps", type=int, default=60)
    exo_group.add_argument("--font", default="M+ 2p heavy")
    exo_group.add_argument("--font-size", type=int, default=60)
    exo_group.add_argument("--y-position", type=float, default=717.0)
    exo_group.add_argument("--no-initial-empty-exo-object", action="store_true")
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
    profile_path = Path(args.profile_output) if args.profile_output else sidecar_base.with_suffix(".profile.csv")
    regroup_profile_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.regroup.csv")
    llm_split_profile_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.llm_split.csv")
    subtitle_timing_profile_path = profile_path.with_name(
        f"{profile_path.stem.removesuffix('.profile')}.subtitle_timing.csv"
    )
    boundary_timing_profile_path = profile_path.with_name(
        f"{profile_path.stem.removesuffix('.profile')}.boundary_timing.csv"
    )
    aligned_text_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.aligned_text.txt")
    final_text_path = profile_path.with_name(f"{profile_path.stem.removesuffix('.profile')}.final_text.txt")
    mistranscription_path = profile_path.with_name(
        f"{profile_path.stem.removesuffix('.profile')}.possible_mistranscriptions.txt"
    )

    try:
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

            transcriber = None
            if args.transcriber_backend == "native":
                if not args.mmproj:
                    print("Error: --mmproj is required for the native Gemma audio backend", file=sys.stderr)
                    return 1
                transcriber = NativeGemmaTranscriber(
                    model_path=Path(args.model),
                    mmproj=Path(args.mmproj),
                    n_gpu_layers=args.n_gpu_layers,
                    ctx_size=args.ctx_size,
                    temp_dir=temp_dir,
                    cli_path=Path(args.llama_mtmd_cli) if args.llama_mtmd_cli else None,
                )
            elif args.transcriber_backend == "server":
                if not args.mmproj:
                    print("Error: --mmproj is required for the server Gemma audio backend", file=sys.stderr)
                    return 1
                transcriber = ServerGemmaTranscriber(
                    model_path=Path(args.model),
                    mmproj=Path(args.mmproj),
                    n_gpu_layers=args.n_gpu_layers,
                    ctx_size=args.ctx_size,
                    temp_dir=temp_dir,
                    server_path=Path(args.llama_server) if args.llama_server else None,
                    host=args.server_host,
                    port=args.server_port,
                    glossary=glossary,
                    max_transcription_split_depth=max(0, args.transcription_max_split_depth),
                )
            else:
                transcriber = GemmaTranscriber(
                    model_path=Path(args.model),
                    mmproj=Path(args.mmproj) if args.mmproj else None,
                    n_gpu_layers=args.n_gpu_layers,
                    ctx_size=args.ctx_size,
                    threads=args.threads or None,
                    batch_size=args.batch_size,
                    temp_dir=temp_dir,
                    sample_rate=sample_rate,
                    verbose=args.verbose,
                )

            try:
                split_size = args.alignment_split_size or ("char" if args.language == "ja" else "word")
                print(
                    "Alignment: "
                    f"model={args.alignment_model}, language={args.language}, "
                    f"split_size={split_size}, star_frequency={args.alignment_star_frequency}",
                    flush=True,
                )
                alignment_config = AlignmentConfig(
                    model_name=args.alignment_model,
                    language=args.language,
                    device=args.alignment_device,
                    split_size=split_size,
                    star_frequency=args.alignment_star_frequency,
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
                )
                if args.profile:
                    _write_aligned_text(aligned_text_path, aligned)
            finally:
                close = getattr(transcriber, "close", None)
                if close is not None:
                    close()

            refiner = None
            if args.cleanup_mode != "off":
                if args.cleanup_model:
                    print("Starting cleanup model...")
                    refiner = LlamaServerTextRefiner(
                        model_path=Path(args.cleanup_model),
                        server_path=Path(args.cleanup_llama_server) if args.cleanup_llama_server else None,
                        glossary=glossary,
                        mode=args.cleanup_mode,
                        host=args.cleanup_server_host,
                        port=args.cleanup_server_port,
                        ctx_size=args.cleanup_ctx_size,
                        n_gpu_layers=args.n_gpu_layers if args.cleanup_n_gpu_layers is None else args.cleanup_n_gpu_layers,
                    )
                else:
                    print("Warning: --cleanup-mode was set but --cleanup-model was not provided; cleanup disabled.")
            try:
                chain_markers: list[ExoMarker] = []
                mistranscription_markers: list[ExoMarker] = []
                subtitles = build_grouped_subtitles(
                    aligned,
                    max_chars=args.max_chars,
                    min_duration=args.min_duration,
                    max_duration=args.max_duration,
                    gap_threshold=args.gap_threshold,
                    regroup=args.regroup_adjacent,
                    regroup_gap_sec=args.regroup_gap_sec,
                    regroup_max_window_sec=args.regroup_max_window_sec,
                    regroup_max_window_chars=args.regroup_max_window_chars,
                    refiner=refiner,
                    cleanup_window_subtitles=args.cleanup_window_subtitles,
                    llm_splitter=refiner if args.llm_split_planning == "cleanup-model" else None,
                    regroup_profile_path=regroup_profile_path if args.profile else None,
                    llm_split_profile_path=llm_split_profile_path if args.llm_split_diagnostics else None,
                    llm_split_console=args.llm_split_diagnostics,
                    chain_markers=chain_markers,
                    subtitle_timing_profile_path=subtitle_timing_profile_path if args.profile else None,
                    boundary_timing_profile_path=boundary_timing_profile_path if args.profile else None,
                    chain_lead_in_sec=max(0.0, args.chain_lead_in_sec),
                    chain_lead_in_growth_sec=max(0.0, args.chain_lead_in_growth_sec),
                    chain_lead_in_max_sec=max(0.0, args.chain_lead_in_max_sec),
                    regroup_ramp_start_sec=max(0.0, args.regroup_ramp_start_sec),
                    regroup_ramp_step_sec=max(0.001, args.regroup_ramp_step_sec),
                    regroup_ramp_max_chain_sec=max(0.0, args.regroup_ramp_max_chain_sec),
                    regroup_ramp_max_chain_tokens=max(0, args.regroup_ramp_max_chain_tokens),
                    llm_max_input_chars=args.llm_split_max_input_chars,
                    llm_second_pass_max_input_chars=args.llm_split_second_pass_max_input_chars,
                )
                if refiner is not None:
                    _write_final_subtitle_text(final_text_path, subtitles)
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
            if args.profile:
                print(f"Wrote profile: {profile_path}")
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
                insert_initial_empty=not args.no_initial_empty_exo_object,
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


def _flag_possible_mistranscriptions(subtitles, refiner, output_path: Path) -> list[ExoMarker]:
    numbered_lines = [(index, sub.text) for index, sub in enumerate(subtitles, start=1)]
    flags = refiner.flag_mistranscriptions(numbered_lines)
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
        by_line.setdefault(flag.line_number, []).append(flag.text)
        if flag.line_number not in marked_lines:
            markers.append(ExoMarker(sub.start_time, sub.end_time))
            marked_lines.add(flag.line_number)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_lines = []
    for line_number in sorted(by_line):
        for text in by_line[line_number]:
            output_lines.append(f"{line_number}\t{text}")
    output_path.write_text("\n".join(output_lines) + ("\n" if output_lines else "NONE\n"), encoding="utf-8")
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
):
    if hasattr(transcriber, "prepare_payload") and hasattr(transcriber, "transcribe_payload"):
        return _transcribe_and_align_server(
            chunks, transcriber, alignment_config, profiler, audio_prep_workers, align_workers
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
