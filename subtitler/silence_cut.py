"""VAD-derived silence cutting, review transport, and timeline remapping."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from .errors import SubtitlerError
from .models import ExoMarker, ExoMediaPlan, ExoMediaSegment, Subtitle
from .transcription_backend import RawVadSpeechInterval


MIN_RAW_SILENCE_SEC = 0.5
TRAILING_PROTECTION_SEC = 0.5
NEXT_SPEECH_LEAD_IN_SEC = 0.2
MIN_PROPOSED_CUT_SEC = 0.5
POLICY_VERSION = 2
FRONTEND_EVENT_PREFIX = "@@SUBUTL_EVENT@@"
MARK_AND_REJECT_TEXT = "無音カット要確認"

CutSilenceMode = Literal["off", "automatic", "review"]
SilenceCutDecision = Literal["accept_cut", "reject_cut", "mark_and_reject"]
EncoderPreset = Literal["hevc-amf-cqp21", "hevc-nvenc-qp21", "hevc-qsv-q21", "libx265-crf21"]
SilenceOutputStrategy = Literal["none", "exo-source", "rendered-mkv"]
FrameRateMode = Literal["reported-cfr", "possible-vfr", "unknown"]

ENCODER_ARGS: dict[str, list[str]] = {
    "hevc-amf-cqp21": [
        "-c:v", "hevc_amf", "-quality", "quality", "-rc", "cqp",
        "-qp_i", "21", "-qp_p", "21", "-qp_b", "21", "-g", "60",
    ],
    "hevc-nvenc-qp21": [
        "-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq",
        "-rc", "constqp", "-qp", "21", "-g", "60",
    ],
    "hevc-qsv-q21": [
        "-c:v", "hevc_qsv", "-preset", "slow", "-global_quality", "21", "-g", "60",
    ],
    "libx265-crf21": [
        "-c:v", "libx265", "-preset", "medium", "-crf", "21", "-g", "60",
    ],
}


@dataclass(frozen=True)
class SilenceCutCandidate:
    id: str
    silence_start: float
    silence_end: float
    cut_start: float
    cut_end: float

    @property
    def cut_duration(self) -> float:
        return max(0.0, self.cut_end - self.cut_start)

    def to_frontend(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "silenceStart": self.silence_start,
            "silenceEnd": self.silence_end,
            "cutStart": self.cut_start,
            "cutEnd": self.cut_end,
            "cutDuration": self.cut_duration,
        }


@dataclass(frozen=True)
class SilenceReviewResult:
    review_id: str
    decisions: dict[str, SilenceCutDecision]


@dataclass(frozen=True)
class MediaStreamSummary:
    has_video: bool
    audio_count: int
    extra_video_count: int
    subtitle_count: int
    data_count: int
    attachment_count: int
    has_chapters: bool
    average_frame_rate: Fraction | None
    nominal_frame_rate: Fraction | None
    time_base: Fraction | None
    frame_rate_mode: FrameRateMode

    @property
    def omitted_descriptions(self) -> list[str]:
        values: list[str] = []
        if self.extra_video_count:
            values.append(f"{self.extra_video_count} additional video stream(s)")
        if self.subtitle_count:
            values.append(f"{self.subtitle_count} subtitle stream(s)")
        if self.data_count:
            values.append(f"{self.data_count} data stream(s)")
        if self.attachment_count:
            values.append(f"{self.attachment_count} attachment stream(s)")
        if self.has_chapters:
            values.append("source chapters")
        return values


@dataclass(frozen=True)
class SilenceCutOutcome:
    subtitles: list[Subtitle]
    chapter_markers: list[ExoMarker]
    qa_markers: list[ExoMarker]
    duration_sec: float
    requested_cuts: list[tuple[float, float]]
    accepted_cuts: list[tuple[float, float]]
    decisions: dict[str, SilenceCutDecision]
    cut_video_path: Path | None
    omitted_streams: list[str]
    output_strategy: SilenceOutputStrategy
    media_source_path: Path | None
    media_plan: ExoMediaPlan | None
    average_frame_rate: Fraction | None
    nominal_frame_rate: Fraction | None
    frame_rate_mode: FrameRateMode


def build_cut_candidates(raw_intervals: Sequence[RawVadSpeechInterval]) -> list[SilenceCutCandidate]:
    ordered = sorted(
        (item for item in raw_intervals if item.end > item.start),
        key=lambda item: (item.start, item.end),
    )
    if len(ordered) < 2:
        return []
    merged: list[RawVadSpeechInterval] = []
    for item in ordered:
        if merged and item.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = RawVadSpeechInterval(previous.start, max(previous.end, item.end))
        else:
            merged.append(item)
    candidates: list[SilenceCutCandidate] = []
    for previous, following in zip(merged, merged[1:]):
        raw_gap = following.start - previous.end
        if raw_gap < MIN_RAW_SILENCE_SEC:
            continue
        cut_start = previous.end + TRAILING_PROTECTION_SEC
        cut_end = following.start - NEXT_SPEECH_LEAD_IN_SEC
        if cut_end - cut_start + 1e-9 < MIN_PROPOSED_CUT_SEC:
            continue
        candidates.append(
            SilenceCutCandidate(
                id=f"silence-{len(candidates) + 1:04d}",
                silence_start=previous.end,
                silence_end=following.start,
                cut_start=cut_start,
                cut_end=cut_end,
            )
        )
    return candidates


def emit_frontend_event(event_type: str, **payload: Any) -> None:
    print(
        FRONTEND_EVENT_PREFIX + json.dumps({"type": event_type, **payload}, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def request_review(candidates: Sequence[SilenceCutCandidate], frontend_protocol: str | None) -> SilenceReviewResult:
    if frontend_protocol != "stdio-v1":
        raise SubtitlerError("Cut silence review mode requires the SubUtl desktop review interface")
    review_id = str(uuid.uuid4())
    emit_frontend_event(
        "silence-review-required",
        reviewId=review_id,
        candidates=[candidate.to_frontend() for candidate in candidates],
    )
    line = sys.stdin.readline()
    if not line:
        raise SubtitlerError("Cut silence review ended before decisions were submitted")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Cut silence review returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("type") != "silence-review-result" or value.get("reviewId") != review_id:
        raise SubtitlerError("Cut silence review response did not match the active review")
    raw_decisions = value.get("decisions")
    if not isinstance(raw_decisions, list):
        raise SubtitlerError("Cut silence review response is missing decisions")
    decisions: dict[str, SilenceCutDecision] = {}
    valid_ids = {candidate.id for candidate in candidates}
    valid_decisions = {"accept_cut", "reject_cut", "mark_and_reject"}
    for item in raw_decisions:
        if not isinstance(item, dict) or item.get("candidateId") not in valid_ids or item.get("decision") not in valid_decisions:
            raise SubtitlerError("Cut silence review response contains an invalid decision")
        candidate_id = str(item["candidateId"])
        if candidate_id in decisions:
            raise SubtitlerError("Cut silence review response contains a duplicate candidate")
        decisions[candidate_id] = item["decision"]
    if set(decisions) != valid_ids:
        raise SubtitlerError("Cut silence review requires a decision for every candidate")
    return SilenceReviewResult(review_id, decisions)


def merge_cut_ranges(ranges: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((start, end) for start, end in ranges if end > start)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class TimelineMap:
    def __init__(self, cuts: Sequence[tuple[float, float]]) -> None:
        self.cuts = merge_cut_ranges(cuts)

    @property
    def removed_duration(self) -> float:
        return sum(end - start for start, end in self.cuts)

    def map_time(self, source_time: float) -> float:
        removed = 0.0
        for start, end in self.cuts:
            if source_time >= end:
                removed += end - start
                continue
            if source_time > start:
                return max(0.0, start - removed)
            break
        return max(0.0, source_time - removed)


def remap_subtitles(subtitles: Sequence[Subtitle], timeline: TimelineMap) -> list[Subtitle]:
    result = copy.deepcopy(list(subtitles))
    for subtitle in result:
        subtitle.start_time = timeline.map_time(subtitle.start_time)
        subtitle.end_time = timeline.map_time(subtitle.end_time)
        for token in subtitle.tokens:
            token.start = timeline.map_time(token.start)
            token.end = timeline.map_time(token.end)
    return result


def remap_markers(markers: Sequence[ExoMarker], timeline: TimelineMap) -> list[ExoMarker]:
    return [
        ExoMarker(timeline.map_time(marker.start_time), timeline.map_time(marker.end_time), marker.text)
        for marker in markers
    ]


def validate_cuts_do_not_overlap_speech(
    cuts: Sequence[tuple[float, float]], raw_intervals: Sequence[RawVadSpeechInterval]
) -> None:
    for cut_start, cut_end in cuts:
        for speech in raw_intervals:
            if cut_start < speech.end and cut_end > speech.start:
                raise SubtitlerError("Refusing to cut a range that overlaps VAD speech")


def probe_media_streams(input_path: Path) -> MediaStreamSummary:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,avg_frame_rate,r_frame_rate,time_base:chapter=start_time",
            "-of", "json", str(input_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SubtitlerError(result.stderr.strip() or "ffprobe failed while preparing Cut silence")
    try:
        value = json.loads(result.stdout)
        streams = value.get("streams", [])
        stream_types = [str(item.get("codec_type", "")) for item in streams]
        video_count = stream_types.count("video")
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        average = _parse_rate(video.get("avg_frame_rate"))
        nominal = _parse_rate(video.get("r_frame_rate"))
        return MediaStreamSummary(
            has_video=video_count > 0,
            audio_count=stream_types.count("audio"),
            extra_video_count=max(0, video_count - 1),
            subtitle_count=stream_types.count("subtitle"),
            data_count=stream_types.count("data"),
            attachment_count=stream_types.count("attachment"),
            has_chapters=bool(value.get("chapters")),
            average_frame_rate=average,
            nominal_frame_rate=nominal,
            time_base=_parse_rate(video.get("time_base")),
            frame_rate_mode=classify_frame_rate(average, nominal),
        )
    except (AttributeError, json.JSONDecodeError, TypeError) as exc:
        raise SubtitlerError("ffprobe returned invalid stream information") from exc


def _parse_rate(value: object) -> Fraction | None:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def classify_frame_rate(average: Fraction | None, nominal: Fraction | None) -> FrameRateMode:
    if average is None or nominal is None:
        return "unknown"
    difference = abs(float(average - nominal))
    tolerance = max(0.001, abs(float(average)) * 0.001)
    return "reported-cfr" if difference <= tolerance else "possible-vfr"


def quantize_cuts_to_source_frames(
    cuts: Sequence[tuple[float, float]], source_fps: Fraction
) -> list[tuple[float, float]]:
    fps = float(source_fps)
    quantized: list[tuple[float, float]] = []
    for start, end in merge_cut_ranges(cuts):
        first_removed = math.ceil(start * fps - 1e-9)
        removed_end = math.floor(end * fps + 1e-9)
        if removed_end <= first_removed:
            continue
        quantized.append((first_removed / fps, removed_end / fps))
    return merge_cut_ranges(quantized)


def build_exo_media_plan(
    source_path: Path,
    duration_sec: float,
    cuts: Sequence[tuple[float, float]],
    source_fps: Fraction,
    project_fps: int,
) -> ExoMediaPlan:
    keeps = keep_ranges(duration_sec, cuts)
    segments: list[ExoMediaSegment] = []
    output_cursor = 1
    cumulative_duration = 0.0
    source_rate = float(source_fps)
    for group_id, (start, end) in enumerate(keeps, 1):
        cumulative_duration += end - start
        cumulative_end = max(output_cursor, int(cumulative_duration * project_fps + 1e-9))
        segments.append(
            ExoMediaSegment(
                output_start_frame=output_cursor,
                output_end_frame=cumulative_end,
                source_start_frame=int(round(start * source_rate)) + 1,
                group_id=group_id,
            )
        )
        output_cursor = cumulative_end + 1
    return ExoMediaPlan(source_path.resolve(), segments)


def build_rendered_media_plan(source_path: Path, duration_sec: float, project_fps: int) -> ExoMediaPlan:
    end_frame = max(1, int(duration_sec * project_fps + 1e-9))
    return ExoMediaPlan(source_path.resolve(), [ExoMediaSegment(1, end_frame, 1, 1)])


def keep_ranges(duration_sec: float, cuts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    position = 0.0
    result: list[tuple[float, float]] = []
    for start, end in merge_cut_ranges(cuts):
        if start > position:
            result.append((position, min(duration_sec, start)))
        position = max(position, end)
    if position < duration_sec:
        result.append((position, duration_sec))
    return [(start, end) for start, end in result if end - start > 1e-6]


def build_filter_script(
    duration_sec: float, cuts: Sequence[tuple[float, float]], audio_count: int, output_fps: int | None = None
) -> str:
    keeps = keep_ranges(duration_sec, cuts)
    if not keeps:
        raise SubtitlerError("Cut silence would remove the entire video")
    lines: list[str] = []
    for index, (start, end) in enumerate(keeps):
        lines.append(f"[0:v:0]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{index}]")
    video_tail = f"fps=fps={output_fps}" if output_fps is not None else "null"
    if len(keeps) == 1:
        lines.append(f"[v0]{video_tail}[vout]")
    else:
        lines.append("".join(f"[v{index}]" for index in range(len(keeps))) + f"concat=n={len(keeps)}:v=1:a=0[vcat]")
        lines.append(f"[vcat]{video_tail}[vout]")
    for audio_index in range(audio_count):
        for keep_index, (start, end) in enumerate(keeps):
            lines.append(
                f"[0:a:{audio_index}]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{audio_index}_{keep_index}]"
            )
        if len(keeps) == 1:
            lines.append(f"[a{audio_index}_0]anull[a{audio_index}out]")
        else:
            lines.append(
                "".join(f"[a{audio_index}_{index}]" for index in range(len(keeps)))
                + f"concat=n={len(keeps)}:v=0:a=1[a{audio_index}out]"
            )
    return ";\n".join(lines) + "\n"


def collision_safe_cut_path(exo_path: Path) -> Path:
    base = exo_path.with_name(f"{exo_path.stem}.cut.mkv")
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = exo_path.with_name(f"{exo_path.stem}.cut.{index}.mkv")
        if not candidate.exists():
            return candidate
    raise SubtitlerError("Could not find an unused Cut silence output filename")


def encode_cut_video(
    input_path: Path,
    exo_path: Path,
    duration_sec: float,
    cuts: Sequence[tuple[float, float]],
    encoder_preset: str,
    output_fps: int = 60,
) -> tuple[Path, list[str]]:
    if encoder_preset not in ENCODER_ARGS:
        raise SubtitlerError("Cut silence requires an explicitly configured encoder preset")
    summary = probe_media_streams(input_path)
    if not summary.has_video:
        raise SubtitlerError("Cut silence requires an input containing a video stream")
    if summary.audio_count < 1:
        raise SubtitlerError("Cut silence requires at least one audio stream")
    destination = collision_safe_cut_path(exo_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    filter_text = build_filter_script(duration_sec, cuts, summary.audio_count, output_fps)
    fd, filter_name = tempfile.mkstemp(prefix="subutl-cut-", suffix=".fffilter")
    os.close(fd)
    filter_path = Path(filter_name)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part.mkv")
    try:
        filter_path.write_text(filter_text, encoding="utf-8")
        command = [
            "ffmpeg", "-y", "-hide_banner", "-i", str(input_path),
            "-filter_complex_script", str(filter_path), "-map", "[vout]",
        ]
        for audio_index in range(summary.audio_count):
            command.extend(["-map", f"[a{audio_index}out]"])
        command.extend(ENCODER_ARGS[encoder_preset])
        command.extend([
            "-pix_fmt", "yuv420p", "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "256k",
            "-map_metadata", "0", "-map_chapters", "-1",
        ])
        for audio_index in range(summary.audio_count):
            command.extend([f"-map_metadata:s:a:{audio_index}", f"0:s:a:{audio_index}"])
        command.append(str(temporary))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise SubtitlerError(result.stderr.strip() or "FFmpeg Cut silence encode failed")
        os.replace(temporary, destination)
        return destination, summary.omitted_descriptions
    finally:
        filter_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def execute_silence_cut(
    *,
    mode: CutSilenceMode,
    candidates: Sequence[SilenceCutCandidate],
    raw_intervals: Sequence[RawVadSpeechInterval],
    subtitles: Sequence[Subtitle],
    chapter_markers: Sequence[ExoMarker],
    qa_markers: Sequence[ExoMarker],
    duration_sec: float,
    input_path: Path,
    exo_path: Path,
    encoder_preset: str | None,
    frontend_protocol: str | None,
    render_cut_video: bool = False,
    project_fps: int = 60,
) -> SilenceCutOutcome:
    if mode == "off":
        return SilenceCutOutcome(
            list(subtitles), list(chapter_markers), list(qa_markers), duration_sec,
            [], [], {}, None, [], "none", None, None, None, None, "unknown",
        )
    if render_cut_video and not encoder_preset:
        raise SubtitlerError("Cut silence requires an explicitly configured encoder preset")
    decisions: dict[str, SilenceCutDecision]
    if mode == "automatic":
        decisions = {candidate.id: "accept_cut" for candidate in candidates}
    else:
        decisions = request_review(candidates, frontend_protocol).decisions if candidates else {}
    requested = merge_cut_ranges(
        (candidate.cut_start, candidate.cut_end)
        for candidate in candidates
        if decisions.get(candidate.id) == "accept_cut"
    )
    validate_cuts_do_not_overlap_speech(requested, raw_intervals)
    summary: MediaStreamSummary | None = None
    accepted = requested
    output_strategy: SilenceOutputStrategy = "none"
    media_plan: ExoMediaPlan | None = None
    media_source_path: Path | None = None
    cut_video: Path | None = None
    omitted: list[str] = []
    if requested:
        summary = probe_media_streams(input_path)
        if not summary.has_video:
            raise SubtitlerError("Cut silence requires an input containing a video stream")
        if summary.audio_count < 1:
            raise SubtitlerError("Cut silence requires at least one audio stream")
        if render_cut_video:
            cut_video, omitted = encode_cut_video(
                input_path, exo_path, duration_sec, requested, encoder_preset or "", project_fps
            )
            media_source_path = cut_video
            output_strategy = "rendered-mkv"
        else:
            if summary.average_frame_rate is None:
                raise SubtitlerError(
                    "Could not determine the source video frame rate for EXO cutting; "
                    "enable Re-encode cut video"
                )
            if summary.frame_rate_mode == "possible-vfr":
                print(
                    "Warning: source frame-rate metadata suggests variable frame rate; "
                    "enabling Re-encode cut video is recommended.",
                    flush=True,
                )
            accepted = quantize_cuts_to_source_frames(requested, summary.average_frame_rate)
            validate_cuts_do_not_overlap_speech(accepted, raw_intervals)
            if accepted:
                media_source_path = input_path.resolve()
                output_strategy = "exo-source"
    timeline = TimelineMap(accepted)
    marked = [
        ExoMarker(candidate.cut_start, candidate.cut_end, MARK_AND_REJECT_TEXT)
        for candidate in candidates
        if decisions.get(candidate.id) == "mark_and_reject"
    ]
    mapped_subtitles = remap_subtitles(subtitles, timeline)
    mapped_chapters = remap_markers(chapter_markers, timeline)
    mapped_qa = remap_markers([*qa_markers, *marked], timeline)
    output_duration = max(0.0, duration_sec - timeline.removed_duration)
    if output_strategy == "exo-source" and summary is not None and summary.average_frame_rate is not None:
        media_plan = build_exo_media_plan(
            input_path, duration_sec, accepted, summary.average_frame_rate, project_fps
        )
    elif output_strategy == "rendered-mkv" and cut_video is not None:
        media_plan = build_rendered_media_plan(cut_video, output_duration, project_fps)
    return SilenceCutOutcome(
        mapped_subtitles,
        mapped_chapters,
        mapped_qa,
        output_duration,
        requested,
        accepted,
        decisions,
        cut_video,
        omitted,
        output_strategy,
        media_source_path,
        media_plan,
        summary.average_frame_rate if summary else None,
        summary.nominal_frame_rate if summary else None,
        summary.frame_rate_mode if summary else "unknown",
    )


def write_silence_manifest(
    path: Path,
    *,
    raw_intervals: Sequence[RawVadSpeechInterval],
    candidates: Sequence[SilenceCutCandidate],
    outcome: SilenceCutOutcome,
    encoder_preset: str | None,
    project_fps: int | None = None,
) -> None:
    payload = {
        "policy": {
            "version": POLICY_VERSION,
            "minimum_raw_silence_sec": MIN_RAW_SILENCE_SEC,
            "minimum_proposed_cut_sec": MIN_PROPOSED_CUT_SEC,
            "trailing_protection_sec": TRAILING_PROTECTION_SEC,
            "next_speech_lead_in_sec": NEXT_SPEECH_LEAD_IN_SEC,
        },
        "raw_vad_speech_intervals": [asdict(item) for item in raw_intervals],
        "candidates": [
            {**asdict(candidate), "cut_duration": candidate.cut_duration, "decision": outcome.decisions.get(candidate.id)}
            for candidate in candidates
        ],
        "output_strategy": outcome.output_strategy,
        "requested_accepted_cuts": [{"start": start, "end": end} for start, end in outcome.requested_cuts],
        "accepted_cuts": [{"start": start, "end": end} for start, end in outcome.accepted_cuts],
        "source_average_frame_rate": str(outcome.average_frame_rate) if outcome.average_frame_rate else None,
        "source_nominal_frame_rate": str(outcome.nominal_frame_rate) if outcome.nominal_frame_rate else None,
        "frame_rate_mode": outcome.frame_rate_mode,
        "project_frame_rate": project_fps,
        "media_source_path": str(outcome.media_source_path) if outcome.media_source_path else None,
        "media_segment_count": len(outcome.media_plan.segments) if outcome.media_plan else 0,
        "encoder_preset": encoder_preset if outcome.output_strategy == "rendered-mkv" else None,
        "ffmpeg_version": _ffmpeg_version() if outcome.output_strategy == "rendered-mkv" else None,
        "cut_video_path": str(outcome.cut_video_path) if outcome.cut_video_path else None,
        "omitted_streams": outcome.omitted_streams,
        "output_duration_sec": outcome.duration_sec,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return next((line for line in result.stdout.splitlines() if line.strip()), "")
