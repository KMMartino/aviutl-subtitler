"""Conservative cross-modal evidence passes for long-form editorial analysis."""

from __future__ import annotations

import base64
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .api_costs import token_cost
from .errors import ModelLoadError, SubtitlerError
from .editorial_locale import output_language_instruction
from .external_transcribers import require_api_key
from .hosted_http import request_json
from .media_analysis import VisualSample, _extract_samples, compare_visual_samples


BURST_PROMPT_VERSION = "editorial-temporal-bursts-v3"
REVIEW_PROMPT_VERSION = "editorial-targeted-review-v2"


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


def analyze_temporal_bursts(
    media_path: Path,
    *,
    duration_ms: int,
    segments: Sequence[dict[str, Any]],
    acoustic_events: Sequence[dict[str, Any]],
    frame_differences: Sequence[dict[str, Any]] = (),
    model: str,
    ffmpeg: str = "ffmpeg",
    output_locale: str = "en",
) -> dict[str, Any]:
    """Inspect three-frame bursts near a bounded set of likely transitions."""
    centers = _transition_centers(duration_ms, segments, acoustic_events, frame_differences)
    if not centers:
        return _empty_usage(BURST_PROMPT_VERSION, "bursts")
    with tempfile.TemporaryDirectory(prefix="subutl_bursts_") as temp_name:
        output_dir = Path(temp_name)
        centers = _refine_deterministic_centers(
            media_path, centers, duration_ms, output_dir, ffmpeg
        )
        samples = _burst_samples(media_path, centers, duration_ms, output_dir, ffmpeg)
        prompt = (
            "Review short ordered three-frame bursts from a long recording. Determine whether each burst "
            "shows a meaningful gameplay/state/scene transition, a continuation, or an ambiguous change. "
            "Focus on stable HUD cues, location/state changes, progress, retries/resets, menus/upgrades, "
            "encounters, reactions, and continuity. Do not invent events between frames. Return strict JSON: "
            '{"bursts":[{"index":0,"description":"","importance":0.0,'
            '"continuity_change":"same|transition|reset_or_retry|unclear","gameplay_state":"",'
            '"tags":[]}]}. '
            + output_language_instruction(output_locale)
        )
        prompt += "\nDeterministic selection evidence: " + json.dumps(
            centers, ensure_ascii=False, separators=(",", ":")
        )
        parsed, input_tokens, output_tokens = _vision_request(model, prompt, samples, _sample_labels(centers))
    results = []
    by_index = {item["index"]: item for item in centers}
    for item in _objects(parsed.get("bursts")):
        index = _integer(item.get("index"), -1)
        center = by_index.get(index)
        if center is None:
            continue
        results.append({
            **center,
            "description": _text(item.get("description"), 1200),
            "importance": _unit(item.get("importance")),
            "continuity_change": _choice(item.get("continuity_change"), {"same", "transition", "reset_or_retry", "unclear"}, "unclear"),
            "gameplay_state": _text(item.get("gameplay_state"), 500),
            "tags": _strings(item.get("tags"), 12, 80),
        })
    return {
        "prompt_version": BURST_PROMPT_VERSION,
        "bursts": results,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": token_cost("openai", model, input_tokens=input_tokens, output_tokens=output_tokens),
    }


def review_editorial_candidates(
    media_path: Path,
    *,
    duration_ms: int,
    recommendations: Sequence[dict[str, Any]],
    transcript: Sequence[dict[str, Any]],
    visual_segments: Sequence[dict[str, Any]],
    temporal_bursts: Sequence[dict[str, Any]],
    acoustic_events: Sequence[dict[str, Any]],
    game_knowledge: str,
    model: str,
    ffmpeg: str = "ffmpeg",
    output_locale: str = "en",
) -> dict[str, Any]:
    """Spend extra vision only on uncertain or consequential proposed edits."""
    candidates = _review_candidates(recommendations, duration_ms)
    if not candidates:
        return _empty_usage(REVIEW_PROMPT_VERSION, "reviews", extra={"creative_suggestions": []})
    centers = [
        {"index": index, "timestamp_ms": (int(item["start_ms"]) + int(item["end_ms"])) // 2, "reason": str(item["id"])}
        for index, item in enumerate(candidates)
    ]
    evidence = []
    for index, item in enumerate(candidates):
        start = int(item["start_ms"])
        end = int(item["end_ms"])
        evidence.append({
            "index": index,
            "recommendation": item,
            "transcript": [row for row in transcript if int(row.get("end_ms", 0)) > start - 15000 and int(row.get("start_ms", 0)) < end + 15000][:80],
            "visuals": [row for row in visual_segments if int(row.get("end_ms", 0)) > start and int(row.get("start_ms", 0)) < end][:20],
            "bursts": [row for row in temporal_bursts if start - 5000 <= int(row.get("timestamp_ms", 0)) <= end + 5000][:8],
            "acoustic_events": [row for row in acoustic_events if int(row.get("end_ms", 0)) > start and int(row.get("start_ms", 0)) < end][:12],
        })
    prompt = f"""Perform a targeted second editorial review. Each numbered candidate has three nearby frames plus transcript, coarse visual, transition-burst, and local acoustic evidence. Acoustic energy is only a prompt to inspect; it is not proof of emotion. Silence is never grounds for cutting.

Output language: {output_language_instruction(output_locale)}

Game knowledge (bounded and possibly uncertain): {game_knowledge}
Candidate evidence: {json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))[:70000]}

For every candidate return one opinionated primary suggestion and one practical backup. Preserve uncertainty. Revise the proposed disposition only when the combined evidence clearly warrants it. Also suggest sparse creative editorial accents when genuinely earned: punch-in, freeze/replay, emphasis text, sound design, or an intentionally literal/misread visual gag tied to actual spoken wording. Never force a joke and never suggest more than two visual gags in this batch.

Return strict JSON:
{{"reviews":[{{"index":0,"verdict":"confirm|revise|unclear","disposition":"keep|condense|omit|connect|review","presentation_mode":"live|live_excerpt|narration_over_source|narration_montage|narration_bridge","primary_suggestion":"","backup_option":"","confidence":0.0,"visual_evidence":""}}],"creative_suggestions":[{{"candidate_index":0,"start_ms":0,"end_ms":0,"type":"punch_in|visual_gag|freeze_frame|reaction_replay|emphasis_text|sound_design|other","suggestion":"","backup_option":"","trigger":"","asset_idea":"","confidence":0.0}}]}}
"""
    with tempfile.TemporaryDirectory(prefix="subutl_review_") as temp_name:
        samples = _burst_samples(media_path, centers, duration_ms, Path(temp_name), ffmpeg)
        parsed, input_tokens, output_tokens = _vision_request(model, prompt, samples, _sample_labels(centers, "candidate"))
    reviews = []
    for item in _objects(parsed.get("reviews")):
        index = _integer(item.get("index"), -1)
        if not 0 <= index < len(candidates):
            continue
        candidate = candidates[index]
        reviews.append({
            "recommendation_id": candidate["id"],
            "verdict": _choice(item.get("verdict"), {"confirm", "revise", "unclear"}, "unclear"),
            "disposition": _choice(item.get("disposition"), {"keep", "condense", "omit", "connect", "review"}, str(candidate.get("disposition") or "review")),
            "presentation_mode": _choice(item.get("presentation_mode"), {"live", "live_excerpt", "narration_over_source", "narration_montage", "narration_bridge"}, str(candidate.get("presentation_mode") or "live_excerpt")),
            "primary_suggestion": _text(item.get("primary_suggestion"), 1800),
            "backup_option": _text(item.get("backup_option"), 1800),
            "confidence": _unit(item.get("confidence")),
            "visual_evidence": _text(item.get("visual_evidence"), 1200),
        })
    creative = []
    valid_types = {"punch_in", "visual_gag", "freeze_frame", "reaction_replay", "emphasis_text", "sound_design", "other"}
    for index, item in enumerate(_objects(parsed.get("creative_suggestions"))):
        candidate_index = _integer(item.get("candidate_index"), -1)
        if not 0 <= candidate_index < len(candidates):
            continue
        candidate = candidates[candidate_index]
        start = max(int(candidate["start_ms"]), _integer(item.get("start_ms"), int(candidate["start_ms"])))
        end = min(int(candidate["end_ms"]), _integer(item.get("end_ms"), int(candidate["end_ms"])))
        if end <= start:
            start, end = int(candidate["start_ms"]), int(candidate["end_ms"])
        creative.append({
            "id": f"{candidate['id']}:creative:{index:03d}",
            "source_id": candidate["source_id"],
            "start_ms": start,
            "end_ms": end,
            "type": _choice(item.get("type"), valid_types, "other"),
            "suggestion": _text(item.get("suggestion"), 1800),
            "backup_option": _text(item.get("backup_option"), 1800),
            "trigger": _text(item.get("trigger"), 800),
            "asset_idea": _text(item.get("asset_idea"), 800),
            "confidence": _unit(item.get("confidence")),
        })
    return {
        "prompt_version": REVIEW_PROMPT_VERSION,
        "reviews": reviews,
        "creative_suggestions": creative,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": token_cost("openai", model, input_tokens=input_tokens, output_tokens=output_tokens),
    }


def apply_targeted_reviews(recommendations: list[dict[str, Any]], reviews: Sequence[dict[str, Any]]) -> None:
    by_id = {str(item.get("id")): item for item in recommendations}
    for review in reviews:
        item = by_id.get(str(review.get("recommendation_id")))
        if item is None:
            continue
        item["targeted_review"] = dict(review)
        if review.get("verdict") == "revise" and float(review.get("confidence", 0.0)) >= 0.8:
            item["disposition"] = review["disposition"]
            item["presentation_mode"] = review["presentation_mode"]
        if str(review.get("primary_suggestion") or "").strip():
            item["reviewed_primary_suggestion"] = review["primary_suggestion"]
        if str(review.get("backup_option") or "").strip():
            item["reviewed_backup_option"] = review["backup_option"]


def _transition_centers(
    duration_ms: int,
    segments: Sequence[dict[str, Any]],
    acoustic: Sequence[dict[str, Any]],
    frame_differences: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    candidates: list[tuple[float, int, str, dict[str, Any]]] = []
    ordered = sorted((item for item in segments if isinstance(item, dict)), key=lambda item: int(item.get("start_ms", 0)))
    for left, right in zip(ordered, ordered[1:]):
        timestamp = int(left.get("end_ms", right.get("start_ms", 0)))
        changed = str(left.get("visual_category")) != str(right.get("visual_category"))
        confidence = min(float(left.get("confidence", 0.0)), float(right.get("confidence", 0.0)))
        candidates.append((1.2 if changed else 0.4 + confidence * 0.3, timestamp, "visual boundary", {}))
    for event in acoustic:
        candidates.append((0.5 + float(event.get("score", 0.0)), int(event.get("start_ms", 0)), "acoustic emphasis", {}))
    for difference in frame_differences:
        score = float(difference.get("change_score", 0.0))
        if score < 0.25:
            continue
        left = int(difference.get("left_ms", 0))
        right = int(difference.get("right_ms", left))
        candidates.append(
            (
                0.9 + score,
                int(difference.get("timestamp_ms", (left + right) // 2)),
                "deterministic frame change",
                {
                    "frame_change_score": round(score, 3),
                    "pixel_difference": difference.get("pixel_difference"),
                    "histogram_difference": difference.get("histogram_difference"),
                    "comparison_left_ms": left,
                    "comparison_right_ms": right,
                    "burst_radius_ms": min(5000, max(900, (right - left) // 6)),
                },
            )
        )
    limit = max(4, min(16, math.ceil(duration_ms / 3_600_000 * 8)))
    selected = []
    for score, timestamp, reason, evidence in sorted(
        candidates, key=lambda item: item[0], reverse=True
    ):
        timestamp = max(800, min(duration_ms - 800, timestamp))
        if timestamp <= 0 or any(abs(timestamp - int(item["timestamp_ms"])) < 10_000 for item in selected):
            continue
        selected.append({"index": len(selected), "timestamp_ms": timestamp, "reason": reason, "selection_score": round(score, 3), **evidence})
        if len(selected) >= limit:
            break
    selected.sort(key=lambda item: int(item["timestamp_ms"]))
    for index, item in enumerate(selected):
        item["index"] = index
    return selected


def _review_candidates(recommendations: Sequence[dict[str, Any]], duration_ms: int) -> list[dict[str, Any]]:
    eligible = [
        item for item in recommendations
        if isinstance(item, dict)
        and item.get("disposition") in {"omit", "condense", "review"}
        and int(item.get("end_ms", 0)) - int(item.get("start_ms", 0)) >= 8000
    ]
    eligible.sort(key=lambda item: (
        0 if item.get("disposition") == "omit" else 1,
        float(item.get("confidence", 0.0)),
        -(int(item.get("end_ms", 0)) - int(item.get("start_ms", 0))),
    ))
    limit = max(4, min(12, math.ceil(duration_ms / 3_600_000 * 6)))
    return eligible[:limit]


def _refine_deterministic_centers(
    media_path: Path,
    centers: Sequence[dict[str, Any]],
    duration_ms: int,
    output_dir: Path,
    ffmpeg: str,
) -> list[dict[str, Any]]:
    """Narrow coarse high-difference intervals locally before paying for vision."""
    refined = [dict(item) for item in centers]
    end_sec = max(0.0, duration_ms / 1000.0 - 0.1)
    for center in refined:
        if center.get("reason") != "deterministic frame change":
            continue
        left = max(0.0, float(center.get("comparison_left_ms", 0)) / 1000.0)
        right = min(end_sec, float(center.get("comparison_right_ms", duration_ms)) / 1000.0)
        try:
            for round_index in range(2):
                if right - left <= 1.4:
                    break
                timestamps = [left + (right - left) * index / 4 for index in range(5)]
                samples = _extract_samples(
                    media_path,
                    media_kind="video",
                    timestamps=timestamps,
                    ffmpeg=ffmpeg,
                    output_dir=output_dir,
                    prefix=f"diff-refine-{center['index']}-{round_index}",
                )
                comparisons = compare_visual_samples(
                    samples, ffmpeg=ffmpeg, output_dir=output_dir
                )
                strongest = max(
                    comparisons,
                    key=lambda item: 0.75 * float(item["pixel_difference"])
                    + 0.25 * float(item["histogram_difference"]),
                )
                left = float(strongest["left_ms"]) / 1000.0
                right = float(strongest["right_ms"]) / 1000.0
            center["timestamp_ms"] = round((left + right) * 500)
            center["comparison_left_ms"] = round(left * 1000)
            center["comparison_right_ms"] = round(right * 1000)
            center["burst_radius_ms"] = max(700, round((right - left) * 500))
            center["deterministic_refinement"] = "two_round_local_frame_diff"
        except (OSError, subprocess.SubprocessError, SubtitlerError, ValueError) as exc:
            center["deterministic_refinement"] = "unavailable"
            center["deterministic_refinement_detail"] = str(exc)[:300]
    return refined


def _burst_samples(media_path: Path, centers: Sequence[dict[str, Any]], duration_ms: int, output_dir: Path, ffmpeg: str) -> list[VisualSample]:
    timestamps = []
    end_sec = max(0.0, duration_ms / 1000.0 - 0.1)
    for center in centers:
        seconds = int(center["timestamp_ms"]) / 1000.0
        radius = max(0.7, float(center.get("burst_radius_ms", 700)) / 1000.0)
        timestamps.extend(max(0.0, min(end_sec, seconds + offset)) for offset in (-radius, 0.0, radius))
    return _extract_samples(media_path, media_kind="video", timestamps=timestamps, ffmpeg=ffmpeg, output_dir=output_dir, prefix="burst")


def _sample_labels(centers: Sequence[dict[str, Any]], noun: str = "burst") -> list[str]:
    result = []
    for center in centers:
        timestamp = int(center["timestamp_ms"])
        for position in ("before", "at", "after"):
            result.append(f"{noun} {center['index']} {position}, centered at {timestamp} ms")
    return result


def _vision_request(model: str, prompt: str, samples: Sequence[VisualSample], labels: Sequence[str]) -> tuple[dict[str, Any], int, int]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for sample, label in zip(samples, labels):
        encoded = base64.b64encode(sample.jpeg_path.read_bytes()).decode("ascii")
        content.extend((
            {"type": "input_text", "text": label},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
        ))
    data = request_json(
        "POST", "https://api.openai.com/v1/responses",
        {"model": model, "reasoning": {"effort": "low"}, "input": [{"role": "user", "content": content}]},
        ModelLoadError, "OpenAI editorial visual review failed",
        headers={"Authorization": f"Bearer {require_api_key('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        timeout_sec=600.0,
    )
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return _response_object(data), int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _response_object(data: dict[str, Any]) -> dict[str, Any]:
    text = ""
    for output in data.get("output", []) if isinstance(data.get("output"), list) else []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) if isinstance(output.get("content"), list) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                text += content["text"]
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3]
    try:
        parsed = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Editorial visual review returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise SubtitlerError("Editorial visual review must return a JSON object")
    return parsed


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


def _empty_usage(version: str, field: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"prompt_version": version, field: [], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    if extra:
        result.update(extra)
    return result


def _objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)][:200] if isinstance(value, list) else []


def _strings(value: Any, limit: int, length: int) -> list[str]:
    return [_text(item, length) for item in value if _text(item, length)][:limit] if isinstance(value, list) else []


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _integer(value: Any, default: int) -> int:
    try:
        return int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _unit(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _choice(value: Any, choices: set[str], fallback: str) -> str:
    text = str(value or "")
    return text if text in choices else fallback
