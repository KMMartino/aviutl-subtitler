"""Deterministic preprocessing for the reference-video research study.

This deliberately stops before the editorial/model stages.  It consumes a
research manifest and produces reusable, app-shaped evidence artifacts.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from subtitler.audio import get_media_duration
from subtitler.editorial_analysis import TranscriptEvidence
from subtitler.media_analysis import compare_visual_samples, sample_media
from subtitler.media_layout import probe_video_geometry

MAX_RESEARCH_WORKERS = 2
PREPROCESSING_VERSION = 1
_TIMESTAMP = re.compile(r"(?P<h>\d{2}:)?(?P<m>\d{2}):(?P<s>\d{2}(?:\.\d{1,3})?)")
_TAG = re.compile(r"<[^>]+>")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _seconds(value: str) -> float:
    match = _TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid WebVTT timestamp: {value!r}")
    hours = int((match.group("h") or "0:")[:-1])
    minutes = int(match.group("m"))
    seconds = float(match.group("s"))
    return hours * 3600 + minutes * 60 + seconds


def parse_vtt(text: str) -> list[TranscriptEvidence]:
    """Parse YouTube's en-orig VTT into the app's normalized evidence type."""
    result: list[TranscriptEvidence] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = (part.strip() for part in line.split("-->", 1))
        right = right.split(" ", 1)[0]
        try:
            start_ms = round(_seconds(left) * 1000)
            end_ms = round(_seconds(right) * 1000)
        except ValueError:
            index += 1
            continue
        index += 1
        cue: list[str] = []
        while index < len(lines) and lines[index].strip():
            cue.append(lines[index].strip())
            index += 1
        # YouTube's rolling captions repeat the previous line in the next cue.
        # Keep only lines carrying new word timestamps. The tiny untimed cues
        # between them are display-state duplicates rather than new speech.
        timed_lines = [value for value in cue if re.search(r"<\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?>", value)]
        if timed_lines:
            cue = timed_lines
        elif end_ms - start_ms <= 100:
            continue
        clean = html.unescape(_TAG.sub("", " ".join(cue))).strip()
        clean = re.sub(r"\s+", " ", clean)
        if clean and end_ms > start_ms:
            result.append(TranscriptEvidence(start_ms, end_ms, clean))
    return result


def transcript_artifacts(items: Iterable[TranscriptEvidence]) -> tuple[str, str]:
    """Return the CSV timing and numbered text formats used by the app."""
    rows = list(items)
    timing = ["start,end\n"] + [f"{item.start_ms / 1000:g},{item.end_ms / 1000:g}\n" for item in rows]
    text = [f"{index}. {item.text}\n" for index, item in enumerate(rows, 1)]
    return "".join(timing), "".join(text)


def _first_file(folder: Path, suffixes: tuple[str, ...]) -> Path | None:
    candidates = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)
    return candidates[0] if candidates else None


def locate_source(folder: Path) -> tuple[Path, Path | None]:
    media = _first_file(folder, (".mp4", ".mkv", ".webm", ".mov", ".avi"))
    if media is None:
        raise FileNotFoundError(f"No downloaded video found under {folder}")
    vtts = sorted(p for p in folder.rglob("*") if p.is_file() and p.name.lower().endswith(".en-orig.vtt"))
    if not vtts:
        vtts = sorted(p for p in folder.rglob("*.vtt") if p.is_file())
    return media, (vtts[0] if vtts else None)


def _source_fingerprint(media: Path, vtt: Path | None) -> dict[str, Any]:
    media_stat = media.stat()
    payload: dict[str, Any] = {
        "media_path": str(media.resolve()),
        "media_size": media_stat.st_size,
        "media_mtime_ns": media_stat.st_mtime_ns,
    }
    if vtt is not None:
        vtt_stat = vtt.stat()
        payload.update({
            "vtt_path": str(vtt.resolve()),
            "vtt_size": vtt_stat.st_size,
            "vtt_mtime_ns": vtt_stat.st_mtime_ns,
        })
    return payload


def _load_reusable_checkpoint(path: Path, fingerprint: dict[str, Any]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("schema_version") != PREPROCESSING_VERSION
        or payload.get("status") != "complete"
        or payload.get("source_fingerprint") != fingerprint
    ):
        return None
    sample_paths = [Path(str(item.get("jpeg_path", ""))) for item in payload.get("samples", [])]
    if not sample_paths or not all(path.is_file() for path in sample_paths):
        return None
    return payload


def process_video(video: dict[str, Any], *, output_root: Path, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    folder = Path(video.get("source_folder") or video.get("folder") or video.get("path", ""))
    media, vtt = locate_source(folder)
    video_id = str(video.get("id") or video.get("video_id") or folder.name)
    out = output_root / video_id
    frames = out / "frames"
    checkpoint = out / "preprocessing.json"
    fingerprint = _source_fingerprint(media, vtt)
    reusable = _load_reusable_checkpoint(checkpoint, fingerprint)
    if reusable is not None:
        return reusable
    duration = get_media_duration(media)
    geometry = probe_video_geometry(media)
    samples = sample_media(media, media_kind="video", duration_sec=duration, detail=str(video.get("detail", "probe")), ffmpeg=ffmpeg, output_dir=frames)
    comparisons = compare_visual_samples(samples, ffmpeg=ffmpeg, output_dir=frames)
    transcript = parse_vtt(vtt.read_text(encoding="utf-8")) if vtt else []
    timing, text = transcript_artifacts(transcript)
    (out / "transcript_timing.csv").parent.mkdir(parents=True, exist_ok=True)
    (out / "transcript_timing.csv").write_text(timing, encoding="utf-8", newline="")
    (out / "transcript_text.txt").write_text(text, encoding="utf-8", newline="")
    payload = {
        "schema_version": PREPROCESSING_VERSION, "status": "complete", "video_id": video_id,
        "source_fingerprint": fingerprint,
        "source_folder": str(folder), "media_path": str(media), "vtt_path": str(vtt) if vtt else None,
        "probe": {"duration_ms": round(duration * 1000), "width": geometry.width, "height": geometry.height, "frame_rate": geometry.frame_rate},
        "transcript": [item.__dict__ for item in transcript],
        "transcript_timing_path": str(out / "transcript_timing.csv"), "transcript_text_path": str(out / "transcript_text.txt"),
        "samples": [{"timestamp_sec": sample.timestamp_sec, "jpeg_path": str(sample.jpeg_path)} for sample in samples],
        "frame_differences": comparisons,
    }
    _atomic_json(checkpoint, payload)
    return payload


def load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    videos = (
        payload.get("videos", payload.get("items", payload))
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(videos, list) or not all(isinstance(item, dict) for item in videos):
        raise ValueError("Research manifest must contain a videos list of objects")
    return videos


def _youtube_id(item: dict[str, Any]) -> str:
    explicit = str(item.get("youtube_id") or "").strip()
    if explicit:
        return explicit
    query = parse_qs(urlparse(str(item.get("url") or "")).query)
    return str((query.get("v") or [""])[0]).strip()


def run(
    manifest: Path,
    output_root: Path,
    *,
    source_root: Path | None = None,
    ffmpeg: str = "ffmpeg",
    workers: int = 1,
    only: set[str] | None = None,
) -> list[dict[str, Any]]:
    videos = []
    for raw in load_manifest(manifest):
        item = dict(raw)
        youtube_id = _youtube_id(item)
        if source_root is not None and youtube_id and not item.get("source_folder"):
            item["source_folder"] = str(source_root / youtube_id)
        item["id"] = str(item.get("key") or item.get("id") or youtube_id)
        item["youtube_id"] = youtube_id
        if only is not None and item["id"] not in only and youtube_id not in only:
            continue
        if "detail" not in item:
            item["detail"] = "precise" if item.get("kind") == "finished" else "detailed"
        videos.append(item)
    workers = max(1, min(MAX_RESEARCH_WORKERS, int(workers)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda item: process_video(item, output_root=output_root, ffmpeg=ffmpeg), videos))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--workers", type=int, default=1, help="Deterministic preprocessing workers (capped at 2)")
    parser.add_argument("--only", action="append", help="Process only this manifest key or YouTube ID (repeatable)")
    args = parser.parse_args()
    run(
        args.manifest,
        args.output,
        source_root=args.source_root,
        ffmpeg=args.ffmpeg,
        workers=args.workers,
        only=set(args.only) if args.only else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
