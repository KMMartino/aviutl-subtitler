"""Persistent, explicitly selected audio assets for editable editorial exports."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .errors import SubtitlerError


def prepare_editorial_audio(
    artifact: dict[str, Any], assets_dir: Path, *, ffmpeg: str = "ffmpeg"
) -> None:
    """Attach export_audio paths in-place; track numbers are zero-based audio indices.

    AviUtl's audio-file object cannot select an embedded stream. PCM sidecars
    retain separate editable channels without re-encoding the source video.
    Legacy/default first-track sources continue referencing their original files.
    """
    for source in artifact.get("sources", []):
        visual = Path(source["visual_path"])
        paired = source.get("media_mode") == "paired"
        spoken = _track(source.get("audio_track", 0))
        game_value = source.get("game_audio_track")
        game = None if game_value is None else _track(game_value)
        if paired:
            face = Path(source["audio_path"])
            speech_on_game = source.get("speech_source") == "gameplay"
            primary_track = (game if game is not None else spoken) if speech_on_game else (game or 0)
            face_track = 0 if speech_on_game else spoken
            primary = _selected_audio(visual, primary_track, assets_dir, ffmpeg)
            overlay = (_selected_audio(visual, spoken, assets_dir, ffmpeg, force=True)
                       if speech_on_game and game is not None and game != spoken
                       else _selected_audio(face, face_track, assets_dir, ffmpeg))
        elif game is not None and game != spoken:
            primary = _selected_audio(visual, game, assets_dir, ffmpeg, force=True)
            overlay = _selected_audio(visual, spoken, assets_dir, ffmpeg, force=True)
        else:
            primary = _selected_audio(visual, spoken, assets_dir, ffmpeg)
            overlay = None
        source["export_audio"] = {
            "primary_path": str(primary.resolve()),
            "overlay_path": str(overlay.resolve()) if overlay is not None else None,
        }


def _track(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise SubtitlerError("Editorial audio tracks must be zero-based nonnegative integers")
    return value


def _selected_audio(
    path: Path, track: int, assets_dir: Path, ffmpeg: str, *, force: bool = False
) -> Path:
    if track == 0 and not force:
        return path
    before = path.stat()
    identity = json.dumps([str(path.resolve()), before.st_size, before.st_mtime_ns, track])
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    assets_dir.mkdir(parents=True, exist_ok=True)
    output = assets_dir / f"audio-{key}-track{track + 1}.wav"
    if output.is_file() and output.stat().st_size > 44:
        return output
    temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.wav")
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
             "-map", f"0:a:{track}", "-vn", "-c:a", "pcm_s16le", "-rf64", "auto",
             "-y", str(temporary)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3600, check=False,
        )
        if completed.returncode or not temporary.is_file() or temporary.stat().st_size <= 44:
            raise SubtitlerError(f"Could not export audio track {track + 1}: {completed.stderr[-1000:]}")
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SubtitlerError("Source media changed while preparing editorial audio")
        temporary.replace(output)
        return output
    finally:
        temporary.unlink(missing_ok=True)
