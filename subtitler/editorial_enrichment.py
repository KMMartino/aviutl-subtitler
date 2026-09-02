"""Conservative cross-modal evidence passes for long-form editorial analysis."""

from __future__ import annotations

import math
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Sequence

import numpy as np





class AcousticEvents(list[dict[str, Any]]):
    def __init__(
        self,
        values: Sequence[dict[str, Any]] = (),
        *,
        status: str,
        detail: str = "",
    ) -> None:
        super().__init__(values)
        self.status = status
        self.detail = detail


def analyze_acoustic_emphasis(
    media_path: Path,
    *,
    duration_ms: int,
    ffmpeg: str = "ffmpeg",
    audio_track: int = 1,
) -> AcousticEvents:
    """Find strong local energy changes without claiming they prove excitement."""
    with tempfile.TemporaryDirectory(prefix="subutl_acoustic_") as temp_name:
        wav_path = Path(temp_name) / "audio.wav"
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(media_path),
            "-map", f"0:a:{max(0, audio_track - 1)}", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", "-y", str(wav_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
        if completed.returncode != 0 or not wav_path.is_file():
            print("Warning: local acoustic emphasis analysis was unavailable; continuing without it.", flush=True)
            return AcousticEvents(status="unavailable", detail=completed.stderr.strip()[:500])
        try:
            with wave.open(str(wav_path), "rb") as handle:
                rate = handle.getframerate()
                samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(np.float32)
        except (OSError, wave.Error) as exc:
            print(f"Warning: local acoustic emphasis analysis failed: {exc}", flush=True)
            return AcousticEvents(status="unavailable", detail=str(exc)[:500])
    if rate <= 0 or samples.size < rate:
        return AcousticEvents(status="complete", detail="audio shorter than analysis window")
    samples /= 32768.0
    window = rate
    hop = rate // 2
    rms = np.asarray([
        float(np.sqrt(np.mean(np.square(samples[start:start + window])) + 1e-12))
        for start in range(0, max(1, samples.size - window + 1), hop)
    ])
    if rms.size < 3:
        return AcousticEvents(status="complete", detail="too few analysis windows")
    db = 20.0 * np.log10(np.maximum(rms, 1e-7))
    median = float(np.median(db))
    mad = max(1.5, float(np.median(np.abs(db - median))) * 1.4826)
    events: list[dict[str, Any]] = []
    for index, value in enumerate(db):
        score = (float(value) - median) / mad
        delta = float(value - db[index - 1]) if index else 0.0
        event_type = ""
        reason = ""
        strength = 0.0
        if score >= 2.8:
            event_type = "energy_peak"
            reason = "Unusually strong vocal/audio energy; inspect for reaction, emphasis, laughter, or impact."
            strength = min(1.0, score / 5.0)
        elif delta >= 8.0 and score >= 1.2:
            event_type = "dynamic_rise"
            reason = "Sudden energy rise; inspect the nearby words and frames for a meaningful turn."
            strength = min(1.0, delta / 16.0)
        if event_type:
            start_ms = round(index * hop / rate * 1000)
            events.append({
                "start_ms": start_ms,
                "end_ms": min(duration_ms, start_ms + 1500),
                "type": event_type,
                "score": round(strength, 3),
                "reason": reason,
            })
    events.extend(_sustained_intensity_events(db, median, mad, hop, rate, duration_ms))
    limit = max(8, min(72, math.ceil(duration_ms / 3_600_000 * 36)))
    events.sort(key=lambda item: (-float(item["score"]), int(item["start_ms"])))
    selected = _spaced_events(events, minimum_gap_ms=2500, limit=limit)
    selected.sort(key=lambda item: int(item["start_ms"]))
    return AcousticEvents(selected, status="complete")






















def _sustained_intensity_events(db: np.ndarray[Any, Any], median: float, mad: float, hop: int, rate: int, duration_ms: int) -> list[dict[str, Any]]:
    high = db >= median + 1.35 * mad
    result = []
    start: int | None = None
    for index, active in enumerate(np.append(high, False)):
        if active and start is None:
            start = index
        elif not active and start is not None:
            if index - start >= 8:
                start_ms = round(start * hop / rate * 1000)
                end_ms = min(duration_ms, round((index * hop + rate) / rate * 1000))
                result.append({"start_ms": start_ms, "end_ms": end_ms, "type": "sustained_intensity", "score": min(1.0, 0.55 + (index - start) / 40), "reason": "Sustained high audio energy; inspect for an extended challenge, reaction, or explanation."})
            start = None
    return result


def _spaced_events(events: Sequence[dict[str, Any]], *, minimum_gap_ms: int, limit: int) -> list[dict[str, Any]]:
    selected = []
    for event in events:
        if any(abs(int(event["start_ms"]) - int(other["start_ms"])) < minimum_gap_ms for other in selected):
            continue
        selected.append(event)
        if len(selected) >= limit:
            break
    return selected
