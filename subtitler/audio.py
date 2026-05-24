"""Audio extraction and WAV helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import AudioExtractionError


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise AudioExtractionError(f"Required executable not found on PATH: {name}")


def extract_audio(input_path: Path, output_wav: Path, audio_track: int = 0) -> None:
    """Extract one audio stream as mono 16 kHz WAV."""
    _require_tool("ffmpeg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        f"0:a:{audio_track}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        "-f",
        "wav",
        str(output_wav),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AudioExtractionError(result.stderr.strip() or "ffmpeg audio extraction failed")


def get_media_duration(input_path: Path) -> float:
    """Return media duration in seconds using ffprobe."""
    if shutil.which("ffprobe") is None:
        return 0.0
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def load_mono_16k_wav(path: Path) -> tuple[Any, int]:
    """Load a mono WAV file as float32 samples."""
    import numpy as np
    import soundfile as sf

    try:
        samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    except Exception as exc:
        raise AudioExtractionError(f"Could not read WAV file: {path}") from exc
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if sample_rate != 16000:
        raise AudioExtractionError(f"Expected 16 kHz WAV, got {sample_rate} Hz: {path}")
    return np.asarray(samples, dtype=np.float32), sample_rate


def write_wav_segment(samples: Any, sample_rate: int, path: Path) -> None:
    import soundfile as sf

    try:
        sf.write(str(path), samples, sample_rate, subtype="PCM_16")
    except Exception as exc:
        raise AudioExtractionError(f"Could not write WAV segment: {path}") from exc
