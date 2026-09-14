"""Local speech activity -> persistent silence ranges -> editable media EXO.

The shelved editorial stage graph is not involved in this composition.
"""
from __future__ import annotations

from array import array
from dataclasses import asdict
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import numpy as np

from .api_usage import ApiUsageLedger
from .editorial_audio import prepare_editorial_audio
from .editorial_cutting import build_human_information_plan
from .editorial_exo import write_editorial_exo_parts
from .editorial_project import load_editorial_checkpoint, write_editorial_checkpoint, unresolved_editorial_sources
from .editorial_resume import relink_matching_editorial_sources
from .errors import SubtitlerError
from .operation_store import OperationStore
from .source_inspection import SourceInspectionRequest, inspect_recording
from .vad import VadSession, _speech_timestamps_from_probabilities

SILENCE_MARKER_VERSION = 1


def collect_voice_activity(path: Path, track: int, duration_ms: int) -> dict[str, Any]:
    """Decode bounded ten-minute blocks; retain compact energy values, not audio."""
    import torch

    rate, block_samples, overlap_samples = 16000, 16000 * 600, 16000 * 2
    session = VadSession()
    previous = np.empty(0, dtype=np.float32)
    speech: list[tuple[int, int]] = []
    levels = array('f')
    offset = 0
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path),
                '-map', f'0:a:{track}', '-vn', '-ac', '1', '-ar', str(rate), '-f', 'f32le', 'pipe:1'],
                stdout=subprocess.PIPE, stderr=errors)
            assert process.stdout is not None
            try:
                while raw := process.stdout.read(block_samples * 4):
                    if len(raw) % 4:
                        raise SubtitlerError('Decoded audio ended inside a sample')
                    samples = np.frombuffer(raw, dtype='<f4').copy()
                    if not np.isfinite(samples).all():
                        raise SubtitlerError('Decoded audio contains invalid samples')
                    padded = np.pad(samples, (0, (-len(samples)) % 160))
                    rms = np.sqrt(np.mean(padded.reshape(-1, 160).astype(np.float64) ** 2, axis=1))
                    levels.extend(np.maximum(-120, 20 * np.log10(np.maximum(rms, 1e-6))).tolist())
                    window = np.concatenate((previous, samples))
                    start = offset - len(previous)
                    probabilities, stride = session.probabilities(window, rate)
                    detected = _speech_timestamps_from_probabilities(probabilities, stride, rate, len(window),
                        max_chunk_sec=610, min_speech_sec=.25, min_silence_ms=100, speech_pad_ms=0)
                    speech.extend((max(0, round((start + row['start']) * 1000 / rate)),
                                   min(duration_ms, round((start + row['end']) * 1000 / rate))) for row in detected)
                    offset += len(samples)
                    previous = samples[-overlap_samples:].copy()
                    print(f'VAD {path.name}: {min(100, offset / rate / max(1, duration_ms / 1000) * 100):.1f}%', flush=True)
                if process.wait() != 0:
                    errors.seek(0)
                    raise SubtitlerError('Audio decode failed: ' + errors.read().decode('utf8', errors='replace')[-1000:])
                if offset == 0:
                    raise SubtitlerError('The selected audio track contains no samples')
            finally:
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
    finally:
        torch.set_num_threads(threads)
    return {'speech': speech, 'energy': {'frame_ms': 10, 'rms_dbfs': levels}}


def run_silence_markers(checkpoint: Path, workspace: Path, *, audio_track: int = 0,
                        game_audio_track: int | None = None, exo: Path | None = None,
                        source_specs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    project = load_editorial_checkpoint(checkpoint)
    if source_specs:
        relink_matching_editorial_sources(project, source_specs)
    if unresolved_editorial_sources(project):
        raise SubtitlerError('Some source files are missing or changed; relink the original media.')
    store = OperationStore(workspace / 'silence-operations', project['project_id'], ApiUsageLedger())
    cuts: list[dict[str, Any]] = []
    export_sources = []
    # Keep any collected editorial data intact. The new composition owns only
    # its independent operation cache, silence_markers field and export records.
    for source in sorted(project['sources'], key=lambda row: row['order']):
        paired = source.get('media_mode') == 'paired'
        voice = Path(source['visual_path'] if source.get('speech_source') == 'gameplay' else source['audio_path'])
        track = 0 if paired else audio_track
        if track < 0 or (game_audio_track is not None and game_audio_track < 0):
            raise SubtitlerError('Audio tracks must be nonnegative')
        paths = {str(Path(source[key]).resolve()): Path(source[key]).stat() for key in ('audio_path', 'visual_path')}
        identity = {'source_id': source['source_id'], 'media': {path: [stat.st_size, stat.st_mtime_ns] for path, stat in paths.items()},
                    'track': track, 'voice': str(voice.resolve()), 'duration_ms': source['duration_ms']}

        def produce() -> dict[str, Any]:
            probe = inspect_recording(SourceInspectionRequest(Path(source['audio_path']), Path(source['visual_path']),
                paired=paired, expected_visual_duration_ms=source['duration_ms']))
            evidence = collect_voice_activity(voice, track, source['duration_ms'])
            plan = build_human_information_plan(project={'sources': [{**source, 'result': {}}]}, synthesis={},
                speech_activity={source['source_id']: evidence['speech']},
                voice_energy={source['source_id']: evidence['energy']})
            for path, stat in paths.items():
                after = Path(path).stat()
                if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    raise SubtitlerError('Source changed during silence detection')
            return {'speech': evidence['speech'], 'cuts': plan['confirmed_cuts'], 'probe': asdict(probe)}

        result = store.execute('silence_markers', SILENCE_MARKER_VERSION, identity, produce, lambda row: row)
        cuts.extend(result['cuts'])
        probe = result['probe']
        visual, face = probe['visual_geometry'] or {}, probe['audio_geometry'] or {}
        exported = {**source, 'audio_track': track, 'game_audio_track': 0 if paired else game_audio_track,
                    'result': {}, 'frame_rate': probe['frame_rate'], 'stages': {},
                    'width': visual.get('width', source.get('width')),
                    'height': visual.get('height', source.get('height')),
                    'audio_width': face.get('width', source.get('audio_width')),
                    'audio_height': face.get('height', source.get('audio_height'))}
        if probe.get('wide_layout'):
            exported['stages'] = {'source_probe': {'output': {'wide_layout': probe['wide_layout']}}}
        export_sources.append(exported)
        project['silence_markers'] = {'version': SILENCE_MARKER_VERSION, 'status': 'in_progress', 'cuts': cuts}
        write_editorial_checkpoint(checkpoint, project)
        print(f"Silence markers: {source['original_name']}: {len(result['cuts'])} ranges", flush=True)
    for i, cut in enumerate(cuts, 1):
        cut['cut_id'] = f'cut-{i:05d}'
    export = {**project, 'sources': export_sources,
              'editorial_map': {'workflow': 'silence_markers', 'confirmed_cuts': cuts}}
    output = exo or checkpoint.with_suffix('.exo')
    prepare_editorial_audio(export, output.with_suffix('.audio'))
    parts = write_editorial_exo_parts(output, export)
    project['silence_markers'] = {'version': SILENCE_MARKER_VERSION, 'status': 'complete', 'cuts': cuts}
    project['outputs'] = {'exo_path': parts[0]['path'], 'exo_parts': parts}
    write_editorial_checkpoint(checkpoint, project)
    print(f'Silence EXO complete: {len(cuts)} blank markers; {len(parts)} file(s); no hosted calls.', flush=True)
    return project
