"""Research-only wrapper around Subtitler's dense visual-learning stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from subtitler.editorial_hosted import _analyze_editorial_visual_windows

MAX_WORKERS = 4
ANALYSIS_VERSION = 1


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


def _identity(source: dict[str, Any], *, model: str, reasoning: str, detail: str, scale: float, workers: int, window_interval_sec: float) -> str:
    relevant = {
        "source_fingerprint": source.get("source_fingerprint"),
        "probe": source.get("probe"),
        "samples": source.get("samples", []),
        "frame_differences": source.get("frame_differences", []),
        "model": model,
        "reasoning": reasoning,
        "detail": detail,
        "sampling_scale": scale,
        "workers": workers,
        "window_interval_sec": window_interval_sec,
        "analysis_version": ANALYSIS_VERSION,
    }
    return hashlib.sha256(json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def analyze_video(
    artifact: Path,
    output_root: Path,
    *,
    model: str = "gpt-5.6-luna",
    reasoning_effort: str = "low",
    detail: str = "detailed",
    sampling_scale: float = 1.5,
    workers: int = 1,
    ffmpeg: str = "ffmpeg",
    window_interval_sec: float = 30.0,
) -> dict[str, Any]:
    source = json.loads(artifact.read_text(encoding="utf-8"))
    video_id = str(source["video_id"])
    workers = max(1, min(MAX_WORKERS, int(workers)))
    identity = _identity(source, model=model, reasoning=reasoning_effort, detail=detail, scale=sampling_scale, workers=workers, window_interval_sec=window_interval_sec)
    out = output_root / video_id
    final_path = out / "visual-learning.json"
    if final_path.is_file():
        try:
            cached = json.loads(final_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if isinstance(cached, dict) and cached.get("cache_identity") == identity and cached.get("status") == "complete":
            return cached
    duration_sec = float(source["probe"]["duration_ms"]) / 1000.0
    progress_path = out / "visual.window_progress.json"
    diagnostics_path = out / "visual.structured_responses.jsonl"
    result = _analyze_editorial_visual_windows(
        media_path=Path(str(source["media_path"])),
        duration_sec=duration_sec,
        detail=detail,
        ffmpeg=ffmpeg,
        sampling_scale=sampling_scale,
        model=model,
        reasoning_effort=reasoning_effort,
        output_locale="en",
        editorial_context="Reference-video editing-style study; describe observable structure and avoid proposing edits.",
        progress_path=progress_path,
        diagnostics_path=diagnostics_path,
        max_workers=workers,
        window_interval_sec=window_interval_sec,
    )
    payload = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "complete",
        "video_id": video_id,
        "cache_identity": identity,
        "source_preprocessing": str(artifact.resolve()),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "detail": detail,
        "sampling_scale": sampling_scale,
        "workers": workers,
        "window_interval_sec": window_interval_sec,
        "window_progress_path": str(progress_path),
        "diagnostics_path": str(diagnostics_path),
        "result": asdict(result),
    }
    _atomic_json(final_path, payload)
    return payload


def run(
    artifacts_root: Path,
    output_root: Path,
    *,
    model: str = "gpt-5.6-luna",
    reasoning_effort: str = "low",
    detail: str = "detailed",
    sampling_scale: float = 1.5,
    workers: int = 1,
    window_workers: int = 1,
    only: set[str] | None = None,
    ffmpeg: str = "ffmpeg",
    window_interval_sec: float = 30.0,
) -> list[dict[str, Any]]:
    paths = sorted(artifacts_root.glob("*/preprocessing.json"))
    if only is not None:
        paths = [path for path in paths if path.parent.name in only]
    if not paths:
        return []
    workers = max(1, min(MAX_WORKERS, int(workers), len(paths)))
    window_workers = max(1, min(int(window_workers), MAX_WORKERS // workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                analyze_video,
                path,
                output_root,
                model=model,
                reasoning_effort=reasoning_effort,
                detail=detail,
                sampling_scale=sampling_scale,
                workers=window_workers,
                ffmpeg=ffmpeg,
                window_interval_sec=window_interval_sec,
            )
            for path in paths
        ]
        return [future.result() for future in futures]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--detail", default="detailed")
    parser.add_argument("--sampling-scale", type=float, default=1.5)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--window-workers", type=int, default=1)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--window-interval", type=float, default=30.0)
    parser.add_argument("--only", action="append")
    args = parser.parse_args()
    run(
        args.artifacts,
        args.output,
        model=args.model,
        reasoning_effort=args.reasoning,
        detail=args.detail,
        sampling_scale=args.sampling_scale,
        workers=args.workers,
        window_workers=args.window_workers,
        only=set(args.only) if args.only else None,
        ffmpeg=args.ffmpeg,
        window_interval_sec=max(0.0, args.window_interval),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
