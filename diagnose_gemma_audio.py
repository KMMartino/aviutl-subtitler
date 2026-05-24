#!/usr/bin/env python3
"""Focused Gemma audio diagnostic for one short input file."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from subtitler.audio import extract_audio, load_mono_16k_wav, write_wav_segment
from subtitler.errors import SubtitlerError
from subtitler.models import AudioChunk
from subtitler.transcriber import GemmaTranscriber


DEFAULT_MODEL = Path(r"C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf")
DEFAULT_MMPROJ = Path(r"C:\coding\0_models\gemma\projectors\proj-for-q6.gguf")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one short audio file through the current Gemma transcription path.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", nargs="?", default="test.m4a", help="Input audio file")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Gemma GGUF model path")
    parser.add_argument("--mmproj", default=str(DEFAULT_MMPROJ), help="Gemma projector path")
    parser.add_argument("--audio-track", type=int, default=0, help="Audio stream index")
    parser.add_argument("--ctx-size", type=int, default=8192)
    parser.add_argument("--n-gpu-layers", type=int, default=-1)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--diagnostics-dir", default="diagnostics")
    parser.add_argument("--keep-wav", action="store_true", help="Keep converted diagnostic WAV")
    parser.add_argument("--verbose", action="store_true", help="Show llama.cpp internals")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(exist_ok=True)

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    wav_path = diagnostics_dir / f"{input_path.stem}_16k_mono.wav"
    log_path = diagnostics_dir / "python_audio_diagnostic.txt"

    try:
        print(f"Input:  {input_path}")
        print(f"WAV:    {wav_path}")
        print(f"Model:  {args.model}")
        print(f"MMProj: {args.mmproj}")
        print("Converting input to mono 16 kHz WAV...")
        extract_audio(input_path, wav_path, args.audio_track)
        samples, sample_rate = load_mono_16k_wav(wav_path)
        duration = len(samples) / sample_rate

        chunk = AudioChunk(
            index=0,
            start=0.0,
            end=duration,
            samples=samples,
            wav_path=wav_path,
        )
        transcriber = GemmaTranscriber(
            model_path=Path(args.model),
            mmproj=Path(args.mmproj) if args.mmproj else None,
            n_gpu_layers=args.n_gpu_layers,
            ctx_size=args.ctx_size,
            threads=args.threads or None,
            batch_size=args.batch_size,
            temp_dir=diagnostics_dir,
            sample_rate=sample_rate,
            verbose=args.verbose,
        )
        transcript = transcriber.transcribe(chunk)
        report = (
            f"input={input_path}\n"
            f"wav={wav_path}\n"
            f"model={args.model}\n"
            f"mmproj={args.mmproj}\n"
            f"duration={duration:.3f}\n"
            f"transcript={transcript.text}\n"
        )
        log_path.write_text(report, encoding="utf-8")
        print("\nTranscript:")
        print(transcript.text)
        print(f"\nWrote diagnostic log: {log_path}")
        if not args.keep_wav:
            wav_path.unlink(missing_ok=True)
        return 0
    except SubtitlerError as exc:
        message = f"Error: {exc}"
        print(message, file=sys.stderr)
        log_path.write_text(message + "\n", encoding="utf-8")
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

