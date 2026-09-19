"""Shared bounded visual analysis execution for editorial and library consumers."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .artifact_io import write_json_artifact
from .editorial_locale import locale_label
from .errors import SubtitlerError
from .media_identity import fingerprint_source
from .media_analysis import (
    BROLL_PROMPT_VERSION, PROMPT_VERSION, AnalysisSegment, MediaAnalysisResult, MediaAnalysisResponseError,
    OpenAIMediaAnalysisProvider, analyze_media,
)

VISUAL_WINDOW_SECONDS = 12 * 60.0
MAX_VISUAL_WORKERS = 3
MAX_VISUAL_SPLIT_DEPTH = 2

def analyze_visual_windows(
    *,
    media_path: Path,
    duration_sec: float,
    detail: str,
    ffmpeg: str,
    sampling_scale: float,
    model: str,
    reasoning_effort: str,
    output_locale: str,
    editorial_context: str,
    progress_path: Path | None = None,
    diagnostics_path: Path | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    max_workers: int | None = None,
    window_interval_sec: float = 0.0,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    profile: str = "editorial",
    stage_version: int = 1,
    provider_factory: Callable[..., Any] = OpenAIMediaAnalysisProvider,
    analyze: Callable[..., MediaAnalysisResult] = analyze_media,
    cancelled: Callable[[], bool] = lambda: False,
) -> MediaAnalysisResult:
    """Build a dense event timeline in bounded requests instead of one giant image call."""
    windows = []
    requested_start = start_sec
    requested_end = duration_sec if end_sec is None else end_sec
    if not 0 <= requested_start < requested_end <= duration_sec:
        raise ValueError("Invalid visual analysis range")
    while start_sec < requested_end:
        window_end = min(requested_end, start_sec + VISUAL_WINDOW_SECONDS)
        windows.append((start_sec, window_end))
        start_sec = window_end
    if not windows:
        windows = [(0.0, max(0.1, duration_sec))]

    signature = {
        "visual_stage_version": stage_version,
        "profile": profile,
        "prompt_version": BROLL_PROMPT_VERSION if profile == "broll" else PROMPT_VERSION,
        "media_identity": asdict(fingerprint_source(media_path)) if media_path.is_file() else str(media_path.resolve()),
        "start_sec": requested_start,
        "end_sec": requested_end,
        "duration_sec": duration_sec,
        "detail": detail,
        "sampling_scale": sampling_scale,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "window_seconds": VISUAL_WINDOW_SECONDS,
        "processing_locale": output_locale,
        "editorial_context": editorial_context,
    }
    cached = _load_visual_window_progress(progress_path, signature)
    progress_lock = threading.Lock()
    request_lock = threading.Lock()
    last_window_started = [0.0]

    def cached_result(start: float, end: float) -> MediaAnalysisResult | None:
        with progress_lock:
            value = cached.get(_visual_window_key(start, end))
        return _media_analysis_result_from_dict(value) if isinstance(value, dict) else None

    def persist_result(start: float, end: float, result: MediaAnalysisResult) -> None:
        if progress_path is None:
            return
        with progress_lock:
            cached[_visual_window_key(start, end)] = asdict(result)
            write_json_artifact(
                progress_path,
                {**signature, "completed_windows": cached},
            )

    def analyze_range(start: float, end: float, split_depth: int = 0) -> MediaAnalysisResult:
        if cancelled():
            raise SubtitlerError("Visual analysis cancelled")
        restored = cached_result(start, end)
        if restored is not None:
            return restored
        provider = provider_factory(
            model,
            output_locale=output_locale,
            editorial_context=editorial_context,
            reasoning_effort=reasoning_effort,
            diagnostics_path=diagnostics_path,
        )
        try:
            result = analyze(
                media_path=media_path,
                media_kind="video",
                duration_sec=duration_sec,
                detail=detail,
                ffmpeg=ffmpeg,
                provider=provider,
                start_sec=start,
                end_sec=end,
                sampling_scale=sampling_scale,
                max_ranges=(min(16, max(6, round((end - start) / 60.0))) if profile == "broll"
                            else min(64, max(12, round((end - start) / 20.0)))),
                include_frame_differences=False,
            )
        except MediaAnalysisResponseError:
            if split_depth >= MAX_VISUAL_SPLIT_DEPTH or end - start < 4 * 60.0:
                raise
            midpoint = start + (end - start) / 2.0
            _print_console_safe(
                locale_label(
                    output_locale,
                    f"Visual learning: retrying {_visual_clock(start)}-{_visual_clock(end)} "
                    "as two smaller structured requests...",
                    f"映像学習: {_visual_clock(start)}-{_visual_clock(end)} を、"
                    "2 件の小さな構造化リクエストに分けて再試行します…",
                ),
            )
            result = _merge_visual_results(
                [
                    analyze_range(start, midpoint, split_depth + 1),
                    analyze_range(midpoint, end, split_depth + 1),
                ],
                prompt_suffix=f"split-recovery-{split_depth + 1}",
            )
        persist_result(start, end, result)
        return result

    def analyze_window(bounds: tuple[float, float]) -> MediaAnalysisResult:
        restored = cached_result(*bounds)
        if restored is not None:
            return restored
        if window_interval_sec > 0:
            with request_lock:
                remaining = float(window_interval_sec) - (time.monotonic() - last_window_started[0])
                if remaining > 0:
                    time.sleep(remaining)
                last_window_started[0] = time.monotonic()
        return analyze_range(*bounds)

    results: dict[int, MediaAnalysisResult] = {}
    requested_workers = MAX_VISUAL_WORKERS if max_workers is None else max(1, int(max_workers))
    workers = min(requested_workers, MAX_VISUAL_WORKERS, len(windows))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="editorial-visual-state")
    futures: dict[Any, int] = {}
    next_index = 0
    completed_ranges = 0
    try:
        while next_index < min(workers, len(windows)):
            futures[pool.submit(analyze_window, windows[next_index])] = next_index
            next_index += 1
        while futures:
            future = next(as_completed(futures))
            index = futures.pop(future)
            result = future.result()
            results[index] = result
            completed_ranges += len(result.segments)
            if progress is not None:
                progress(len(results), len(windows), completed_ranges)
            if next_index < len(windows):
                futures[pool.submit(analyze_window, windows[next_index])] = next_index
                next_index += 1
    except BaseException:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)

    ordered = [results[index] for index in range(len(windows))]
    described = [
        MediaAnalysisResult(
            description=(
                f"{_visual_clock(windows[index][0])}-{_visual_clock(windows[index][1])}: "
                f"{result.description}"
            ),
            tags=result.tags,
            segments=result.segments,
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            sample_count=result.sample_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            frame_differences=result.frame_differences,
        )
        for index, result in enumerate(ordered)
    ]
    result = _merge_visual_results(described, prompt_suffix=f"windowed-{profile}-v3")
    if media_path.is_file():
        from .visual_evidence_store import publish_visual_evidence
        publish_visual_evidence(media_path, result, profile=profile, stage_version=stage_version,
                                start_sec=requested_start, end_sec=requested_end)
    return result


def _visual_window_key(start_sec: float, end_sec: float) -> str:
    return f"{start_sec:.3f}-{end_sec:.3f}"


def _load_visual_window_progress(
    path: Path | None, signature: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in signature.items()):
        return {}
    completed = value.get("completed_windows")
    if not isinstance(completed, dict):
        return {}
    return {
        str(key): item
        for key, item in completed.items()
        if isinstance(item, dict)
    }


def _media_analysis_result_from_dict(value: dict[str, Any]) -> MediaAnalysisResult | None:
    try:
        segments = [
            AnalysisSegment(
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                description=str(item.get("description") or ""),
                tags=[str(tag) for tag in item.get("tags", [])],
                confidence=float(item.get("confidence", 0.0)),
                motion_level=(
                    float(item["motion_level"])
                    if item.get("motion_level") is not None
                    else None
                ),
                visual_category=str(item.get("visual_category") or "other"),
                suitability=str(item.get("suitability") or ""),
                observed_label=str(item.get("observed_label") or ""),
                evidence_spacing_sec=float(item.get("evidence_spacing_sec") or 0),
                handoff_required=bool(item.get("handoff_required")),
                handoff_reason=str(item.get("handoff_reason") or ""),
            )
            for item in value.get("segments", [])
            if isinstance(item, dict)
        ]
        return MediaAnalysisResult(
            description=str(value["description"]),
            tags=[str(tag) for tag in value.get("tags", [])],
            segments=segments,
            provider=str(value["provider"]),
            model=str(value["model"]),
            prompt_version=str(value["prompt_version"]),
            sample_count=int(value.get("sample_count", 0)),
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            cost_usd=float(value.get("cost_usd", 0.0)),
            frame_differences=[
                dict(item) for item in value.get("frame_differences", []) if isinstance(item, dict)
            ],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _merge_visual_results(
    values: list[MediaAnalysisResult], *, prompt_suffix: str
) -> MediaAnalysisResult:
    if not values:
        raise SubtitlerError("Visual analysis produced no completed windows")
    tags = list(
        dict.fromkeys(
            tag for result in values for tag in result.tags if str(tag).strip()
        )
    )
    return MediaAnalysisResult(
        description="\n".join(
            result.description for result in values if result.description.strip()
        )[:48_000],
        tags=tags[:120],
        segments=[segment for result in values for segment in result.segments],
        provider=values[0].provider,
        model=values[0].model,
        prompt_version=f"{values[0].prompt_version}-{prompt_suffix}",
        sample_count=sum(result.sample_count for result in values),
        input_tokens=sum(result.input_tokens for result in values),
        output_tokens=sum(result.output_tokens for result in values),
        cost_usd=sum(result.cost_usd for result in values),
        frame_differences=[
            difference for result in values for difference in result.frame_differences
        ],
    )


def _visual_clock(seconds: float) -> str:
    total = max(0, round(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _print_console_safe(message: str) -> None:
    """Keep retry recovery working on legacy Windows consoles."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)
