"""Audio extraction and WAV helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .errors import AudioExtractionError


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise AudioExtractionError(f"Required executable not found on PATH: {name}")


def _parse_ffmpeg_time_seconds(line: str) -> float | None:
    key, _, value = line.strip().partition("=")
    if not value:
        return None
    if key in {"out_time_us", "out_time_ms"}:
        try:
            return max(0.0, float(value) / 1_000_000.0)
        except ValueError:
            return None
    if key != "out_time":
        return None
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def extract_audio(
    input_path: Path,
    output_wav: Path,
    audio_track: int = 0,
    duration: float = 0.0,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Extract one audio stream as mono 16 kHz WAV."""
    _require_tool("ffmpeg")
    cmd = [
        "ffmpeg",
        "-y",
        "-nostats",
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
        "-progress",
        "pipe:1",
        str(output_wav),
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stderr_lines: list[str] = []

    def read_stderr() -> None:
        for item in process.stderr:
            stderr_lines.append(item)

    import threading

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    for line in process.stdout:
        if duration > 0 and progress_callback is not None:
            seconds = _parse_ffmpeg_time_seconds(line)
            if seconds is not None:
                progress_callback(min(100.0, seconds / duration * 100.0))
    code = process.wait()
    stderr_thread.join(timeout=1.0)
    if code != 0:
        raise AudioExtractionError("".join(stderr_lines).strip() or "ffmpeg audio extraction failed")
    if progress_callback is not None:
        progress_callback(100.0)


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
