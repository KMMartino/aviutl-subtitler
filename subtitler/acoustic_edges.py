"""Local, deterministic gap edges measured on the selected voice track.

VAD/transcript intervals are hard protection. Energy can extend their protection,
never move a cut into them. Ambiguous or continually noisy edges use fixed handles.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from .api_usage import ApiUsageLedger
from .errors import SubtitlerError
from .operation_store import OperationStore
from .speech_gaps import SpeechGap

FRAME_MS = 10


def measure_voice_energy(path: Path, track: int) -> dict[str, Any]:
    """Decode once, stream bounded blocks, retain only 10 ms RMS measurements."""
    levels: list[float] = []
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-map', f'0:a:{track}',
             '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=errors,
        )
        assert process.stdout is not None
        pending = b''
        try:
            while block := process.stdout.read(640 * 1000):
                pending += block
                size = len(pending) // 640 * 640
                if size:
                    frames = np.frombuffer(pending[:size], dtype='<f4').reshape(-1, 160)
                    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
                    levels.extend(np.maximum(-120, 20 * np.log10(np.maximum(rms, 1e-6))).round(3).tolist())
                    pending = pending[size:]
            if process.wait() != 0:
                errors.seek(0)
                raise SubtitlerError('Voice energy extraction failed: ' + errors.read().decode('utf8', errors='replace')[-1000:])
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    if not levels or not all(np.isfinite(levels)):
        raise SubtitlerError('Voice energy extraction returned no valid measurements')
    return {'schema_version': 1, 'frame_ms': FRAME_MS, 'rms_dbfs': levels,
            'source_path': str(path.resolve()), 'audio_track': track}


def load_voice_energy(path: Path, track: int, workspace: Path) -> dict[str, Any]:
    stat = path.stat()
    inputs = {'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'track': track}
    store = OperationStore(workspace, 'voice-energy-v1', ApiUsageLedger())

    def produce() -> dict[str, Any]:
        result = measure_voice_energy(path, track)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise SubtitlerError('Voice source changed during acoustic analysis')
        return result

    return store.execute('voice_energy', 1, inputs, produce, lambda value: value)


def refine_gap(gap: SpeechGap, energy: dict[str, Any], *, duration: float) -> tuple[float, float, dict[str, Any]]:
    """Find 60 ms settled floor regions within 750 ms of each protected edge.

    A local lower quantile estimates the floor; a speech/floor contrast check
    avoids chasing music or a noise gate with no useful speech evidence. A 20 ms
    residual handle protects the short measurement window. Interior sounds do
    not become editorial decisions: this operation only adjusts gap edges.
    """
    step = energy['frame_ms'] / 1000
    first, last = int(np.ceil(gap.silence_start / step)), int(gap.silence_end / step)
    last = min(last, len(energy['rms_dbfs']))
    origin = max(0, first - 30)
    values = np.asarray(energy['rms_dbfs'][origin:last + 30], dtype=float)
    first, last = first - origin, last - origin
    middle = values[first:last]
    detail: dict[str, Any] = {'method': 'local_noise_floor', 'frame_ms': energy['frame_ms'],
                              'raw_gap': [gap.silence_start, gap.silence_end]}
    if len(middle) < 20 or not np.isfinite(middle).all():
        return gap.cut_start, gap.cut_end, {**detail, 'fallback': 'insufficient_measurements'}
    floor = float(np.percentile(middle, 20))
    threshold = floor + 6
    detail.update(noise_floor_dbfs=round(floor, 2), threshold_dbfs=round(threshold, 2))
    settle = max(1, round(.060 / step))
    search = max(settle, round(.750 / step))

    def edge(left: bool) -> tuple[float, str]:
        anchor = first if left else last
        fallback = gap.cut_start if left else gap.cut_end
        if (left and gap.silence_start == 0) or (not left and gap.silence_end >= duration):
            return (0 if left else duration), 'source_edge'
        speech = values[max(0, anchor - 30):anchor] if left else values[anchor:min(len(values), anchor + 30)]
        if not len(speech) or float(np.percentile(speech, 80)) < floor + 10:
            return fallback, 'fixed_handle_insufficient_contrast'
        stop = min(last - settle, first + search) if left else max(first, last - search)
        positions = range(first, stop + 1) if left else range(last - settle, stop - 1, -1)
        for index in positions:
            if np.all(values[index:index + settle] <= threshold):
                # Require a settled region beyond the residual handle.
                return (((index + origin) * step + .020) if left else ((index + origin + settle) * step - .020)), 'settled_noise_floor'
        return fallback, 'fixed_handle_unsettled_noise'

    start, start_basis = edge(True)
    end, end_basis = edge(False)
    detail.update(start_basis=start_basis, end_basis=end_basis)
    return max(gap.silence_start, start), min(gap.silence_end, end), detail
