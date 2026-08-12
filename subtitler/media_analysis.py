"""Sampled visual analysis for media-library assets."""

from __future__ import annotations

import argparse
import base64
import json
import math
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

import numpy as np

from .api_costs import token_cost
from .env import load_env_file
from .errors import ModelLoadError, SubtitlerError
from .editorial_locale import output_language_instruction
from .external_transcribers import require_api_key
from .hosted_http import request_json


DETAIL_MULTIPLIERS = {
    "simple": 1,
    "medium": 2,
    "detailed": 5,
    "precise": 10,
}
DETAIL_OPTIONS = (*DETAIL_MULTIPLIERS, "probe")
MAX_COARSE_PROBES = 100
MIN_TRANSITION_BUDGET = 16
MAX_TRANSITION_BUDGET = 96
MAX_FRAME_EXTRACTION_WORKERS = 4
SECONDS_PER_TRANSITION = 150.0
PROMPT_VERSION = "media-analysis-v7"
RESULT_PREFIX = "@@SUBUTL_MEDIA_ANALYSIS@@"


@dataclass(frozen=True)
class VisualSample:
    index: int
    timestamp_sec: float
    jpeg_path: Path


@dataclass(frozen=True)
class SamplingPlan:
    coarse_count: int
    adaptive: bool
    breakpoint_precision_sec: float | None
    refinement_rounds: int
    max_boundaries: int


@dataclass(frozen=True)
class BoundaryProbe:
    boundary_index: int
    probe_position: int
    timestamp_sec: float
    jpeg_path: Path
    left_description: str
    left_category: str
    right_description: str
    right_category: str


@dataclass
class BoundaryBracket:
    coarse_boundary_index: int
    boundary_index: int
    left_sec: float
    right_sec: float
    left_range: AnalyzedRange
    right_range: AnalyzedRange


@dataclass(frozen=True)
class BoundaryClassification:
    scene: str
    scene_id: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    confidence: float = 0.7
    motion_level: float | None = None
    visual_category: str = "other"
    suitability: str = ""


@dataclass(frozen=True)
class RefinedBoundary:
    coarse_boundary_index: int
    timestamp_sec: float
    left_range: AnalyzedRange
    right_range: AnalyzedRange


@dataclass(frozen=True)
class AnalyzedRange:
    start_index: int
    end_index: int
    description: str
    tags: list[str]
    confidence: float
    motion_level: float | None
    visual_category: str
    suitability: str


@dataclass(frozen=True)
class AnalysisSegment:
    start_ms: int
    end_ms: int
    description: str
    tags: list[str]
    confidence: float
    motion_level: float | None
    visual_category: str
    suitability: str


@dataclass(frozen=True)
class MediaAnalysisResult:
    description: str
    tags: list[str]
    segments: list[AnalysisSegment]
    provider: str
    model: str
    prompt_version: str
    sample_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    frame_differences: list[dict[str, Any]] = field(default_factory=list)


class MediaAnalysisProvider(Protocol):
    provider: str
    model: str

    def analyze(self, samples: Sequence[VisualSample], media_kind: str, title: str, max_ranges: int) -> tuple[str, list[str], list[AnalyzedRange], int, int]: ...

    def refine_boundaries(self, probes: Sequence[BoundaryProbe]) -> tuple[dict[tuple[int, int], BoundaryClassification], int, int]: ...


class OpenAIMediaAnalysisProvider:
    provider = "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        *,
        editorial_context: str = "",
        output_locale: str = "en",
    ) -> None:
        self.model = model
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
        self.editorial_context = editorial_context.strip()[:12000]
        self.output_locale = output_locale

    def analyze(
        self,
        samples: Sequence[VisualSample],
        media_kind: str,
        title: str,
        max_ranges: int,
    ) -> tuple[str, list[str], list[AnalyzedRange], int, int]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Analyze this media as an editor searching for useful B-roll, not as a literal scene "
                    "captioner. The overall description must emphasize what the asset is useful for, its tone, "
                    "and likely editorial roles (for example explanation, dramatic emphasis, comedy, atmosphere, "
                    "transition, or illustrative gameplay). Mention identifying subjects only when useful for "
                    "retrieval. For video, group adjacent samples into chronological ranges whenever their "
                    "editorial role is the same. Clearly separate gameplay, trailers/cinematics, talking heads, "
                    "menus/UI, standalone effects, artwork, and unusable material so an editor can avoid presenter "
                    "shots when selecting gameplay. Avoid narrating incidental objects or frame-by-frame action. "
                    f"Use no more than {max_ranges} broad ranges. A single range for genuinely continuous gameplay or a "
                    "consistent talking-head section is correct; do not invent changes merely because time passed. "
                    "Cover the sampled timeline in chronological order. "
                    "Do not infer ownership or usage rights. Return strict JSON only as "
                    '{"description":"broad editorial summary","tags":["retrieval tag"],"ranges":['
                    '{"start_index":0,"end_index":1,"description":"broad content and editorial role",'
                    '"tags":["tag"],"confidence":0.0,"motion_level":0.0,'
                    '"visual_category":"gameplay|trailer|cinematic|talking_head|art|ui|effect|unusable|other",'
                    '"suitability":"where and how this range could be used"}]}. '
                    f"Media kind: {media_kind}. Filename title: {title}. "
                    + (
                        f"Project/game context: {self.editorial_context}. "
                        if self.editorial_context
                        else ""
                    )
                    + output_language_instruction(self.output_locale)
                    + " Each following image is labeled with its sample index and timestamp."
                ),
            }
        ]
        for sample in samples:
            encoded = base64.b64encode(sample.jpeg_path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "input_text",
                    "text": f"Sample index {sample.index}, timestamp {sample.timestamp_sec:.3f} seconds.",
                }
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "low",
                }
            )
        data = request_json(
            "POST",
            "https://api.openai.com/v1/responses",
            {
                "model": self.model,
                "reasoning": {"effort": "low"},
                "input": [{"role": "user", "content": content}],
            },
            ModelLoadError,
            "OpenAI media analysis failed",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout_sec=300.0,
        )
        text = _response_text(data)
        parsed = _json_object(text)
        ranges = _parse_ranges(parsed.get("ranges"), len(samples))
        usage = data.get("usage") or {}
        return (
            str(parsed.get("description") or "").strip()[:4000],
            _tags(parsed.get("tags")),
            ranges,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )

    def refine_boundaries(
        self,
        probes: Sequence[BoundaryProbe],
    ) -> tuple[dict[tuple[int, int], BoundaryClassification], int, int]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Refine coarse editorial boundaries in a long video. Each boundary has two ordered probes "
                    "at one-third and two-thirds between a known left scene and right scene. Classify each probe "
                    "as left, right, or new when it belongs to a genuinely different intermediate scene. Reuse "
                    "the same short scene_id for two probes showing the same new scene. Different new scenes "
                    "must receive different scene_id values. Use broad editorial role and content type, not "
                    "incidental objects, and do not invent cuts in continuous footage. For new scenes include "
                    "a concise editorial description, category, suitability, and retrieval tags. Return strict "
                    "JSON only as "
                    '{"decisions":[{"boundary_index":0,"probe_position":0,'
                    '"scene":"left|right|new","scene_id":"middle-1","description":"",'
                    '"visual_category":"gameplay|trailer|cinematic|talking_head|art|ui|effect|unusable|other",'
                    '"suitability":"","tags":[],"confidence":0.0,"motion_level":0.0}]}. '
                    + output_language_instruction(self.output_locale)
                ),
            }
        ]
        for probe in probes:
            encoded = base64.b64encode(probe.jpeg_path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        f"Boundary {probe.boundary_index}, probe {probe.probe_position} at "
                        f"{probe.timestamp_sec:.3f}s. "
                        f"LEFT [{probe.left_category}]: {probe.left_description}. "
                        f"RIGHT [{probe.right_category}]: {probe.right_description}."
                    ),
                }
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "low",
                }
            )
        data = request_json(
            "POST",
            "https://api.openai.com/v1/responses",
            {
                "model": self.model,
                "reasoning": {"effort": "low"},
                "input": [{"role": "user", "content": content}],
            },
            ModelLoadError,
            "OpenAI boundary refinement failed",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout_sec=300.0,
        )
        parsed = _json_object(_response_text(data))
        decisions: dict[tuple[int, int], BoundaryClassification] = {}
        valid_probes = {(probe.boundary_index, probe.probe_position) for probe in probes}
        raw_decisions = parsed.get("decisions")
        if isinstance(raw_decisions, list):
            for item in raw_decisions:
                if not isinstance(item, dict):
                    continue
                try:
                    boundary_index = int(item.get("boundary_index"))
                    probe_position = int(item.get("probe_position"))
                except (TypeError, ValueError):
                    continue
                key = (boundary_index, probe_position)
                scene = str(item.get("scene") or "").strip().lower()
                if key not in valid_probes or scene not in {"left", "right", "new"}:
                    continue
                decisions[key] = BoundaryClassification(
                    scene=scene,
                    scene_id=str(item.get("scene_id") or "").strip()[:80],
                    description=str(item.get("description") or "").strip()[:1000],
                    tags=tuple(_tags(item.get("tags"))),
                    confidence=_unit_float(item.get("confidence")) or 0.7,
                    motion_level=_optional_unit_float(item.get("motion_level")),
                    visual_category=str(item.get("visual_category") or "other").strip()[:80],
                    suitability=str(item.get("suitability") or "").strip()[:1000],
                )
        usage = data.get("usage") or {}
        return (
            decisions,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )


def analyze_media(
    *,
    media_path: Path,
    media_kind: Literal["video", "image"],
    duration_sec: float | None,
    detail: str = "simple",
    ffmpeg: str,
    provider: MediaAnalysisProvider,
    start_sec: float = 0.0,
    end_sec: float | None = None,
) -> MediaAnalysisResult:
    if not media_path.is_file():
        raise SubtitlerError(f"Media asset no longer exists: {media_path}")
    analysis_start, analysis_end = _analysis_bounds(duration_sec, start_sec, end_sec, media_kind)
    analysis_duration = analysis_end - analysis_start if media_kind == "video" else duration_sec
    with tempfile.TemporaryDirectory(prefix="subutl_analysis_") as temp_name:
        output_dir = Path(temp_name)
        plan = _sampling_plan(analysis_duration, detail)
        samples = sample_media(
            media_path,
            media_kind=media_kind,
            duration_sec=duration_sec,
            detail=detail,
            ffmpeg=ffmpeg,
            output_dir=output_dir,
            start_sec=analysis_start,
            end_sec=analysis_end,
        )
        description, tags, ranges, input_tokens, output_tokens = provider.analyze(
            samples,
            media_kind,
            media_path.stem,
            plan.max_boundaries + 1,
        )
        try:
            frame_differences = compare_visual_samples(
                samples, ffmpeg=ffmpeg, output_dir=output_dir
            )
        except (OSError, subprocess.SubprocessError, SubtitlerError) as exc:
            print(f"Warning: deterministic frame comparison was unavailable: {exc}", flush=True)
            frame_differences = []
        if not description:
            raise SubtitlerError("Media analysis returned no overall description")
        ranges = ranges[:plan.max_boundaries + 1]
        refined_boundaries: list[RefinedBoundary] = []
        refined_sample_count = 0
        if media_kind == "video" and plan.adaptive and len(ranges) > 1:
            (
                refined_boundaries,
                refined_sample_count,
                refinement_input_tokens,
                refinement_output_tokens,
            ) = _refine_boundaries(
                media_path,
                samples=samples,
                ranges=ranges,
                plan=plan,
                ffmpeg=ffmpeg,
                output_dir=output_dir,
                provider=provider,
            )
            input_tokens += refinement_input_tokens
            output_tokens += refinement_output_tokens
        segments = (
            _segments_from_ranges(
                samples,
                ranges,
                analysis_end,
                refined_boundaries,
                timeline_start_sec=analysis_start,
            )
            if media_kind == "video"
            else []
        )
        return MediaAnalysisResult(
            description=description,
            tags=tags,
            segments=segments,
            provider=provider.provider,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            sample_count=len(samples) + refined_sample_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=token_cost(
                provider.provider,
                provider.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            frame_differences=frame_differences,
        )


def compare_visual_samples(
    samples: Sequence[VisualSample],
    *,
    ffmpeg: str,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Measure adjacent low-resolution luma changes as bounded transition evidence."""
    if len(samples) < 2:
        return []
    jobs = list(enumerate(samples))
    workers = min(MAX_FRAME_EXTRACTION_WORKERS, len(jobs))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="frame-compare") as pool:
        frames = list(
            pool.map(
                lambda job: _sample_luma(job[1], job[0], ffmpeg, output_dir),
                jobs,
            )
        )
    raw: list[tuple[float, float, float]] = []
    for left, right in zip(frames, frames[1:]):
        mad = float(np.mean(np.abs(right.astype(np.float32) - left.astype(np.float32))) / 255.0)
        left_hist = np.bincount(left, minlength=256).astype(np.float64)
        right_hist = np.bincount(right, minlength=256).astype(np.float64)
        histogram = float(np.sum(np.abs(left_hist - right_hist)) / (2.0 * left.size))
        raw.append((0.75 * mad + 0.25 * histogram, mad, histogram))
    scores = np.asarray([item[0] for item in raw], dtype=np.float64)
    median = float(np.median(scores))
    spread = max(0.01, float(np.median(np.abs(scores - median))) * 1.4826)
    result = []
    for index, ((combined, mad, histogram), left, right) in enumerate(
        zip(raw, samples, samples[1:])
    ):
        relative = max(0.0, (combined - median) / spread)
        result.append(
            {
                "index": index,
                "left_ms": round(left.timestamp_sec * 1000),
                "right_ms": round(right.timestamp_sec * 1000),
                "timestamp_ms": round((left.timestamp_sec + right.timestamp_sec) * 500),
                "pixel_difference": round(mad, 4),
                "histogram_difference": round(histogram, 4),
                "change_score": round(min(1.0, relative / 4.0), 3),
            }
        )
    return result


def _sample_luma(
    sample: VisualSample,
    index: int,
    ffmpeg: str,
    output_dir: Path,
) -> np.ndarray[Any, Any]:
    output = output_dir / f"luma-{index:03d}.raw"
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(sample.jpeg_path),
            "-vf",
            "scale=64:36,format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-y",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or not output.is_file():
        raise SubtitlerError(f"Could not compare sampled frame: {completed.stderr.strip()}")
    data = np.frombuffer(output.read_bytes(), dtype=np.uint8)
    if data.size != 64 * 36:
        raise SubtitlerError("Sampled frame comparison returned an unexpected size")
    return data


def sample_media(
    media_path: Path,
    *,
    media_kind: Literal["video", "image"],
    duration_sec: float | None,
    detail: str = "simple",
    ffmpeg: str,
    output_dir: Path,
    start_sec: float = 0.0,
    end_sec: float | None = None,
) -> list[VisualSample]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if media_kind == "image":
        timestamps = [0.0]
    else:
        bounded_end = end_sec if end_sec is not None else duration_sec
        window_duration = None if bounded_end is None else bounded_end - start_sec
        timestamps = [start_sec + timestamp for timestamp in _sample_timestamps(window_duration, detail)]
    return _extract_samples(
        media_path,
        media_kind=media_kind,
        timestamps=timestamps,
        ffmpeg=ffmpeg,
        output_dir=output_dir,
        prefix="sample",
    )


def _extract_samples(
    media_path: Path,
    *,
    media_kind: Literal["video", "image"],
    timestamps: Sequence[float],
    ffmpeg: str,
    output_dir: Path,
    prefix: str,
) -> list[VisualSample]:
    jobs = list(enumerate(timestamps))
    if not jobs:
        return []
    workers = min(MAX_FRAME_EXTRACTION_WORKERS, len(jobs))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="frame-sample") as pool:
        return list(
            pool.map(
                lambda job: _extract_sample(
                    media_path,
                    media_kind=media_kind,
                    index=job[0],
                    timestamp=job[1],
                    ffmpeg=ffmpeg,
                    output_dir=output_dir,
                    prefix=prefix,
                ),
                jobs,
            )
        )


def _extract_sample(
    media_path: Path,
    *,
    media_kind: Literal["video", "image"],
    index: int,
    timestamp: float,
    ffmpeg: str,
    output_dir: Path,
    prefix: str,
) -> VisualSample:
    output = output_dir / f"{prefix}-{index:03d}.jpg"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if media_kind == "video":
        command.extend(["-ss", f"{timestamp:.3f}"])
    command.extend(
        [
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(768,iw)':-2:out_range=full,format=yuvj420p",
            "-q:v",
            "4",
            "-y",
            str(output),
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode != 0 or not output.is_file():
        raise SubtitlerError(
            f"Could not sample media at {timestamp:.3f}s: {completed.stderr.strip()}"
        )
    return VisualSample(index, timestamp, output)


def _sample_timestamps(duration_sec: float | None, detail: str = "simple") -> list[float]:
    if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
        return [0.0]
    count = _sampling_plan(duration_sec, detail).coarse_count
    end = max(0.0, duration_sec - 0.1)
    if count == 1:
        return [0.0]
    return [end * index / (count - 1) for index in range(count)]


def _sampling_plan(duration_sec: float | None, detail: str = "simple") -> SamplingPlan:
    if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
        return SamplingPlan(1, False, None, 0, 0)
    if detail != "probe":
        multiplier = DETAIL_MULTIPLIERS.get(detail, DETAIL_MULTIPLIERS["simple"])
        count = math.floor(_base_standard_frame_count(duration_sec) * multiplier + 0.5)
        return SamplingPlan(max(1, count), False, None, 0, MIN_TRANSITION_BUDGET)
    coarse_count = _probe_frame_count(duration_sec)
    precision = min(3.0, max(0.25, duration_sec / 400.0))
    spacing = duration_sec / max(1, coarse_count - 1)
    rounds = max(0, math.ceil(math.log(spacing / precision, 3)))
    return SamplingPlan(coarse_count, True, precision, rounds, _transition_budget(duration_sec))


def _transition_budget(duration_sec: float) -> int:
    return min(
        MAX_TRANSITION_BUDGET,
        max(MIN_TRANSITION_BUDGET, math.ceil(duration_sec / SECONDS_PER_TRANSITION)),
    )


def _base_standard_frame_count(duration_sec: float) -> float:
    if duration_sec <= 2.0:
        return 1.0
    if duration_sec <= 120.0:
        return _interpolate(duration_sec, 2.0, 1.0, 120.0, 10.0)
    if duration_sec <= 1200.0:
        return _interpolate(duration_sec, 120.0, 10.0, 1200.0, 40.0)
    return 40.0 * duration_sec / 1200.0


def _probe_frame_count(duration_sec: float) -> int:
    if duration_sec <= 2.0:
        return 4
    if duration_sec <= 120.0:
        count = _interpolate(duration_sec, 2.0, 4.0, 120.0, 10.0)
    elif duration_sec <= 1200.0:
        count = _interpolate(duration_sec, 120.0, 10.0, 1200.0, 40.0)
    else:
        count = min(MAX_COARSE_PROBES, 40.0 * math.sqrt(duration_sec / 1200.0))
    return max(2, math.floor(count + 0.5))


def _interpolate(value: float, x1: float, y1: float, x2: float, y2: float) -> float:
    return y1 + (value - x1) / (x2 - x1) * (y2 - y1)


def _refine_boundaries(
    media_path: Path,
    *,
    samples: Sequence[VisualSample],
    ranges: Sequence[AnalyzedRange],
    plan: SamplingPlan,
    ffmpeg: str,
    output_dir: Path,
    provider: MediaAnalysisProvider,
) -> tuple[list[RefinedBoundary], int, int, int]:
    refiner = getattr(provider, "refine_boundaries", None)
    if not callable(refiner) or plan.breakpoint_precision_sec is None:
        return [], 0, 0, 0
    brackets: list[BoundaryBracket] = []
    next_boundary_index = 0
    for boundary_index, (left_range, right_range) in enumerate(zip(ranges, ranges[1:])):
        left_sec = samples[left_range.end_index].timestamp_sec
        right_sec = samples[right_range.start_index].timestamp_sec
        if right_sec <= left_sec:
            continue
        brackets.append(
            BoundaryBracket(
                boundary_index,
                next_boundary_index,
                left_sec,
                right_sec,
                left_range,
                right_range,
            )
        )
        next_boundary_index += 1
        if len(brackets) >= plan.max_boundaries:
            break
    total_samples = 0
    input_tokens = 0
    output_tokens = 0
    finalized: list[BoundaryBracket] = []
    new_scene_cache: dict[tuple[int, str], AnalyzedRange] = {}
    for round_index in range(plan.refinement_rounds):
        active: list[BoundaryBracket] = []
        for bracket in brackets:
            if bracket.right_sec - bracket.left_sec > plan.breakpoint_precision_sec:
                active.append(bracket)
            else:
                finalized.append(bracket)
        if not active:
            brackets = []
            break
        timestamps = [
            timestamp
            for bracket in active
            for timestamp in (
                bracket.left_sec + (bracket.right_sec - bracket.left_sec) / 3,
                bracket.left_sec + 2 * (bracket.right_sec - bracket.left_sec) / 3,
            )
        ]
        extracted = _extract_samples(
            media_path,
            media_kind="video",
            timestamps=timestamps,
            ffmpeg=ffmpeg,
            output_dir=output_dir,
            prefix=f"refine-{round_index}",
        )
        probes: list[BoundaryProbe] = []
        for bracket_index, bracket in enumerate(active):
            for probe_position in range(2):
                sample = extracted[bracket_index * 2 + probe_position]
                probes.append(
                    BoundaryProbe(
                        boundary_index=bracket.boundary_index,
                        probe_position=probe_position,
                        timestamp_sec=sample.timestamp_sec,
                        jpeg_path=sample.jpeg_path,
                        left_description=bracket.left_range.description,
                        left_category=bracket.left_range.visual_category,
                        right_description=bracket.right_range.description,
                        right_category=bracket.right_range.visual_category,
                    )
                )
        decisions, round_input_tokens, round_output_tokens = refiner(probes)
        total_samples += len(probes)
        input_tokens += round_input_tokens
        output_tokens += round_output_tokens
        next_brackets: list[BoundaryBracket] = []
        for active_index, bracket in enumerate(active):
            probe_times = timestamps[active_index * 2:active_index * 2 + 2]
            probe_ranges = [
                _range_for_classification(
                    decisions.get((bracket.boundary_index, probe_position)),
                    bracket,
                    new_scene_cache,
                    probe_position,
                )
                for probe_position in range(2)
            ]
            point_times = [bracket.left_sec, *probe_times, bracket.right_sec]
            point_ranges = [bracket.left_range, *probe_ranges, bracket.right_range]
            for point_index in range(3):
                left_range = point_ranges[point_index]
                right_range = point_ranges[point_index + 1]
                if _same_scene(left_range, right_range):
                    continue
                next_brackets.append(
                    BoundaryBracket(
                        coarse_boundary_index=bracket.coarse_boundary_index,
                        boundary_index=next_boundary_index,
                        left_sec=point_times[point_index],
                        right_sec=point_times[point_index + 1],
                        left_range=left_range,
                        right_range=right_range,
                    )
                )
                next_boundary_index += 1
                if len(finalized) + len(next_brackets) >= plan.max_boundaries:
                    break
            if len(finalized) + len(next_brackets) >= plan.max_boundaries:
                break
        brackets = next_brackets
    finalized.extend(brackets)
    return (
        [
            RefinedBoundary(
                coarse_boundary_index=bracket.coarse_boundary_index,
                timestamp_sec=(bracket.left_sec + bracket.right_sec) / 2,
                left_range=bracket.left_range,
                right_range=bracket.right_range,
            )
            for bracket in sorted(
                finalized[:plan.max_boundaries],
                key=lambda item: (item.coarse_boundary_index, item.left_sec),
            )
        ],
        total_samples,
        input_tokens,
        output_tokens,
    )


def _range_for_classification(
    classification: BoundaryClassification | None,
    bracket: BoundaryBracket,
    cache: dict[tuple[int, str], AnalyzedRange],
    probe_position: int,
) -> AnalyzedRange:
    if classification is None:
        return bracket.left_range if probe_position == 0 else bracket.right_range
    if classification.scene == "left":
        return bracket.left_range
    if classification.scene == "right":
        return bracket.right_range
    scene_id = classification.scene_id or f"probe-{bracket.boundary_index}-{probe_position}"
    key = (bracket.coarse_boundary_index, scene_id.casefold())
    existing = cache.get(key)
    if existing is not None:
        return existing
    category = classification.visual_category or "other"
    discovered = AnalyzedRange(
        start_index=0,
        end_index=0,
        description=classification.description or f"Intermediate {category.replace('_', ' ')} scene",
        tags=list(classification.tags),
        confidence=classification.confidence,
        motion_level=classification.motion_level,
        visual_category=category,
        suitability=classification.suitability,
    )
    cache[key] = discovered
    return discovered


def _same_scene(left: AnalyzedRange, right: AnalyzedRange) -> bool:
    if left is right:
        return True
    return (
        left.visual_category.casefold(),
        left.description.casefold(),
        left.suitability.casefold(),
    ) == (
        right.visual_category.casefold(),
        right.description.casefold(),
        right.suitability.casefold(),
    )


def _segments_from_ranges(
    samples: Sequence[VisualSample],
    ranges: Sequence[AnalyzedRange],
    duration_sec: float | None,
    refined_boundaries: Sequence[RefinedBoundary] | None = None,
    *,
    timeline_start_sec: float = 0.0,
) -> list[AnalysisSegment]:
    if not samples or not ranges:
        return []
    duration = max(samples[-1].timestamp_sec + 0.1, duration_sec or 0.0)
    grouped: dict[int, list[RefinedBoundary]] = {}
    for boundary in refined_boundaries or ():
        grouped.setdefault(boundary.coarse_boundary_index, []).append(boundary)
    timeline_ranges = [ranges[0]]
    boundary_times: list[float] = []
    for coarse_index, (left_range, right_range) in enumerate(zip(ranges, ranges[1:])):
        refinements = sorted(grouped.get(coarse_index, []), key=lambda item: item.timestamp_sec)
        if not refinements:
            refinements = [
                RefinedBoundary(
                    coarse_boundary_index=coarse_index,
                    timestamp_sec=(
                        samples[left_range.end_index].timestamp_sec
                        + samples[right_range.start_index].timestamp_sec
                    ) / 2,
                    left_range=left_range,
                    right_range=right_range,
                )
            ]
        for boundary in refinements:
            current = timeline_ranges[-1]
            if _same_scene(current, boundary.right_range):
                continue
            boundary_times.append(boundary.timestamp_sec)
            timeline_ranges.append(boundary.right_range)
        if not _same_scene(timeline_ranges[-1], right_range):
            boundary_times.append(refinements[-1].timestamp_sec)
            timeline_ranges.append(right_range)
    result: list[AnalysisSegment] = []
    for range_index, analyzed_range in enumerate(timeline_ranges):
        if not analyzed_range.description:
            continue
        start = timeline_start_sec if range_index == 0 else boundary_times[range_index - 1]
        end = duration if range_index == len(timeline_ranges) - 1 else boundary_times[range_index]
        if end <= start:
            end = start + 0.1
        result.append(
            AnalysisSegment(
                start_ms=max(0, int(round(start * 1000))),
                end_ms=max(1, int(round(end * 1000))),
                description=analyzed_range.description,
                tags=analyzed_range.tags,
                confidence=analyzed_range.confidence,
                motion_level=analyzed_range.motion_level,
                visual_category=analyzed_range.visual_category,
                suitability=analyzed_range.suitability,
            )
        )
    return result


def _analysis_bounds(
    duration_sec: float | None,
    start_sec: float,
    end_sec: float | None,
    media_kind: str,
) -> tuple[float, float]:
    if media_kind == "image":
        return 0.0, 0.0
    duration = duration_sec or 0.0
    effective_end = duration if end_sec is None else end_sec
    if (
        not math.isfinite(start_sec)
        or not math.isfinite(effective_end)
        or start_sec < 0
        or effective_end <= start_sec
        or (duration_sec is not None and effective_end > duration_sec + 0.001)
    ):
        raise SubtitlerError("Analysis range must be inside the video and have a positive duration")
    return start_sec, effective_end


def _parse_ranges(value: Any, sample_count: int) -> list[AnalyzedRange]:
    if not isinstance(value, list):
        return []
    result: list[AnalyzedRange] = []
    previous_end = -1
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            start_index = int(item.get("start_index"))
            end_index = int(item.get("end_index"))
        except (TypeError, ValueError):
            continue
        if (
            start_index < 0
            or end_index < start_index
            or end_index >= sample_count
            or start_index <= previous_end
        ):
            continue
        previous_end = end_index
        motion = _optional_unit_float(item.get("motion_level"))
        result.append(
            AnalyzedRange(
                start_index=start_index,
                end_index=end_index,
                description=str(item.get("description") or "").strip()[:2000],
                tags=_tags(item.get("tags")),
                confidence=_unit_float(item.get("confidence")),
                motion_level=motion,
                visual_category=str(item.get("visual_category") or "").strip()[:100],
                suitability=str(item.get("suitability") or "").strip()[:1000],
            )
        )
    return result


def _response_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for output in data.get("output") or []:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def _json_object(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3]
    try:
        parsed = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Media analysis returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise SubtitlerError("Media analysis response must be a JSON object")
    return parsed


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()[:100]
        if text and text.casefold() not in {existing.casefold() for existing in result}:
            result.append(text)
        if len(result) >= 30:
            break
    return result


def _unit_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number)) if math.isfinite(number) else 0.0


def _optional_unit_float(value: Any) -> float | None:
    return None if value is None else _unit_float(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--kind", required=True, choices=["video", "image"])
    parser.add_argument("--duration-sec", type=float)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--end-sec", type=float)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--model", required=True)
    parser.add_argument("--detail", choices=DETAIL_OPTIONS, default="simple")
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()
    try:
        load_env_file(Path(args.env_file))
        result = analyze_media(
            media_path=Path(args.asset),
            media_kind=args.kind,
            duration_sec=args.duration_sec,
            detail=args.detail,
            ffmpeg=args.ffmpeg,
            provider=OpenAIMediaAnalysisProvider(args.model),
            start_sec=args.start_sec,
            end_sec=args.end_sec,
        )
        print(RESULT_PREFIX + json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(RESULT_PREFIX + json.dumps({"error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
