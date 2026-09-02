"""Research-only per-video editorial analysis.

This is intentionally separate from the production long-stream runner.  It
consumes the deterministic artifacts made by ``research_harness.py`` and asks
one hosted vision-capable model for a bounded description of the video's
editing structure.  Every request and result is checkpointed per source so a
later synthesis pass can reuse the paid work.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from subtitler.api_costs import token_cost
from subtitler.errors import ModelLoadError, SubtitlerError
from subtitler.external_transcribers import require_api_key
from subtitler.hosted_http import request_json

MAX_WORKERS = 4
ANALYSIS_VERSION = 4
PROMPT_VERSION = "reference-editorial-analysis-v5"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "video_summary": {"type": "string"},
        "format": {
            "type": "string",
            "enum": [
                "cold_open",
                "cold_open_then_narrated_setup",
                "narrated_open",
                "gameplay_first",
                "mixed",
                "unknown",
            ],
        },
        "opening": {"type": "string"},
        "narration_usage": {"type": "string", "enum": ["none", "opening_only", "opening_and_middle", "interwoven", "unknown"]},
        "gameplay_editing": {"type": "string", "enum": ["raw_light_cuts", "selective_highlights", "montage_heavy", "narration_led", "unknown"]},
        "pacing": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_index": {"type": "integer"},
                    "end_index": {"type": "integer"},
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "editorial_role": {"type": "string", "enum": ["hook", "setup", "progression", "payoff", "reaction", "transition", "recap", "other"]},
                    "confidence": {"type": "number"},
                },
                "required": ["start_index", "end_index", "start_sec", "end_sec", "title", "description", "editorial_role", "confidence"],
                "additionalProperties": False,
            },
        },
        "notable_patterns": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["video_summary", "format", "opening", "narration_usage", "gameplay_editing", "pacing", "beats", "notable_patterns", "uncertainties"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are studying finished gaming videos and their source VODs to inform a long-form editorial tool.
Describe observable editing practice, not a proposed edit for the current application. Use the transcript as
supporting evidence and the ordered frames as primary visual evidence. Distinguish a cold open, narrated setup,
raw gameplay, highlight selection, montage, and narration-led interleaving. A VOD may contain dead air or menus;
do not call it a failed video. Do not infer success or failure of a game unless the evidence establishes it.
Spoken streamer commentary is not post-recorded narration. Treat speech as narration only when the curator notes,
source/VOD comparison, or clear production evidence establishes voiceover. Curator observations are reliable
study labels: use the evidence to locate and explain them rather than contradicting them from speech alone.
For an item whose kind is "vod", classify only the source VOD itself. Relationship fields such as finished_key,
vod_key, and vod_keys identify paired material; they do not transfer the finished video's opening, narration,
editing style, or outcome into the VOD. Do not infer a finished-version production choice from those identifiers.
Return only the requested JSON object. Keep beats broad enough to be useful as a style reference (normally 4-24
per video), and state uncertainty instead of inventing details."""


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


def _response_object(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("status") == "incomplete":
        raise SubtitlerError("Reference analysis returned incomplete output")
    chunks: list[str] = []
    for output in data.get("output", []) if isinstance(data.get("output"), list) else []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) if isinstance(output.get("content"), list) else []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise SubtitlerError("Reference analysis was refused")
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    try:
        value = json.loads("".join(chunks).strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Reference analysis returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise SubtitlerError("Reference analysis must return a JSON object")
    return value


def _validate_result(value: dict[str, Any], sample_count: int, duration_sec: float) -> None:
    # The API schema provides shape validation; these checks protect the
    # durable artifact from an otherwise structurally valid but unusable answer.
    required = {"video_summary", "format", "opening", "narration_usage", "gameplay_editing", "pacing", "beats", "notable_patterns", "uncertainties"}
    if not required.issubset(value):
        raise SubtitlerError("Reference analysis omitted required fields")
    if not isinstance(value["beats"], list):
        raise SubtitlerError("Reference analysis beats must be an array")
    previous = -1
    for beat in value["beats"]:
        if not isinstance(beat, dict):
            raise SubtitlerError("Reference analysis contains an invalid beat")
        start = int(beat["start_index"])
        end = int(beat["end_index"])
        if start < 0 or end < start or end >= max(1, sample_count):
            raise SubtitlerError("Reference analysis beat index is outside sampled evidence")
        if start < previous:
            raise SubtitlerError("Reference analysis beats are not chronological")
        if float(beat["start_sec"]) < 0 or float(beat["end_sec"]) < float(beat["start_sec"]) or float(beat["end_sec"]) > duration_sec + 2:
            raise SubtitlerError("Reference analysis beat timestamp is outside source duration")
        previous = start


def _transcript_excerpt(rows: list[dict[str, Any]], limit: int = 120_000) -> str:
    lines = [
        f"[{float(row.get('start_ms', 0)) / 1000:.1f}-{float(row.get('end_ms', 0)) / 1000:.1f}] "
        f"{str(row.get('text', '')).strip()}"
        for row in rows
    ]
    if sum(len(line) + 1 for line in lines) <= limit:
        return "\n".join(lines)

    # Long VODs must not silently become opening-only studies. Preserve local
    # transcript continuity while distributing the request budget across the
    # complete source timeline.
    band_count = min(12, len(lines))
    band_budget = max(1, (limit - (band_count * 48)) // band_count)
    selected: list[str] = []
    for band in range(band_count):
        start = round(band * len(lines) / band_count)
        end = round((band + 1) * len(lines) / band_count)
        selected.append(f"[transcript coverage band {band + 1}/{band_count}]")
        used = 0
        for line in lines[start:end]:
            if used + len(line) + 1 > band_budget:
                selected.append("[remainder of this coverage band omitted for request size]")
                break
            selected.append(line)
            used += len(line) + 1
    return "\n".join(selected)


def _select_samples(samples: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    """Keep chronological coverage when a deterministic run produced many frames."""
    if len(samples) <= maximum:
        return samples
    indexes = {round(index * (len(samples) - 1) / (maximum - 1)) for index in range(maximum)}
    return [sample for index, sample in enumerate(samples) if index in indexes]


def _visual_learning_context(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "No dense visual-learning artifact is available yet.", "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Dense visual-learning artifact was unreadable.", "invalid"
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return "Dense visual-learning artifact has no completed result.", "incomplete"
    identity = str(payload.get("cache_identity") or "")
    segments = result.get("segments") if isinstance(result.get("segments"), list) else []
    lines = ["Dense visual-learning timeline (primary visual evidence):"]
    for item in _select_samples(segments, 300):
        if not isinstance(item, dict):
            continue
        lines.append(
            f"[{float(item.get('start_ms', 0)) / 1000:.1f}-{float(item.get('end_ms', 0)) / 1000:.1f}] "
            f"{str(item.get('visual_category') or 'other')}: {str(item.get('description') or '').strip()}"
        )
    differences = result.get("frame_differences") if isinstance(result.get("frame_differences"), list) else []
    lines.append(f"Deterministic frame-change comparisons available: {len(differences)}.")
    return "\n".join(lines), identity or "present"


def analyze_video(
    artifact: Path,
    output_root: Path,
    *,
    model: str = "gpt-5.6-luna",
    reasoning_effort: str = "medium",
    max_frames: int = 160,
    curator_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = json.loads(artifact.read_text(encoding="utf-8"))
    video_id = str(source["video_id"])
    out = output_root / video_id
    result_path = out / "reference-analysis.json"
    request_path = out / "reference-analysis.request.json"
    visual_text, visual_identity = _visual_learning_context(artifact.parent / "visual-learning.json")
    source_identity = hashlib.sha256(artifact.read_bytes()).hexdigest()
    curated = curator_context or {}
    cache_identity = hashlib.sha256(json.dumps({"source": source_identity, "visual": visual_identity, "model": model, "reasoning": reasoning_effort, "max_frames": max_frames, "prompt_version": PROMPT_VERSION, "curator_context": curated}, sort_keys=True).encode("utf-8")).hexdigest()
    if result_path.is_file():
        cached = json.loads(result_path.read_text(encoding="utf-8"))
        if cached.get("analysis_version") == ANALYSIS_VERSION and cached.get("prompt_version") == PROMPT_VERSION and cached.get("cache_identity") == cache_identity:
            return cached
    samples = _select_samples(list(source.get("samples", [])), max(1, max_frames))
    prompt_text = (
        f"Study video {video_id}. Duration: {float(source['probe']['duration_ms']) / 1000:.1f} seconds. "
        f"There are {len(samples)} chronological visual samples.\n\nTranscript:\n{_transcript_excerpt(source.get('transcript', []))}\n\n"
        f"{visual_text}\n\n"
        f"Curator context (reliable study labels):\n{json.dumps(curated, ensure_ascii=False, indent=2)}\n\n"
        "For each supplied image, the label gives its sample index and timestamp. Analyze the complete video, "
        "not only the most exciting samples."
    )
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt_text}]
    request_record = {"model": model, "reasoning_effort": reasoning_effort, "prompt_version": PROMPT_VERSION, "cache_identity": cache_identity, "visual_learning_identity": visual_identity, "video_id": video_id, "prompt_text": prompt_text, "sample_paths": []}
    for sample in samples:
        frame = Path(str(sample["jpeg_path"]))
        encoded = base64.b64encode(frame.read_bytes()).decode("ascii")
        content.extend((
            {"type": "input_text", "text": f"Sample {sample.get('timestamp_sec', 0):.3f}s (index {len(request_record['sample_paths'])})."},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
        ))
        request_record["sample_paths"].append(str(frame))
    _atomic_json(request_path, request_record)
    payload = request_json(
        "POST", "https://api.openai.com/v1/responses",
        {"model": model, "instructions": SYSTEM_PROMPT, "reasoning": {"effort": reasoning_effort}, "input": [{"role": "user", "content": content}], "max_output_tokens": 12_000, "text": {"format": {"type": "json_schema", "name": "reference_editorial_analysis", "strict": True, "schema": RESPONSE_SCHEMA}}},
        ModelLoadError,
        "OpenAI reference editorial analysis failed",
        headers={"Authorization": f"Bearer {require_api_key('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        timeout_sec=900.0,
    )
    result = _response_object(payload)
    _validate_result(result, len(samples), float(source["probe"]["duration_ms"]) / 1000)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    record = {"analysis_version": ANALYSIS_VERSION, "prompt_version": PROMPT_VERSION, "cache_identity": cache_identity, "visual_learning_identity": visual_identity, "status": "complete", "video_id": video_id, "model": model, "reasoning_effort": reasoning_effort, "sample_count": len(samples), "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": token_cost("openai", model, input_tokens=input_tokens, output_tokens=output_tokens), "result": result}
    _atomic_json(result_path, record)
    return record


def _manifest_contexts(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", payload.get("videos", [])) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("key") or item.get("id")): item
        for item in items
        if isinstance(item, dict) and (item.get("key") or item.get("id"))
    }


def run(artifacts_root: Path, output_root: Path, *, model: str = "gpt-5.6-luna", reasoning_effort: str = "medium", workers: int = 1, only: set[str] | None = None, manifest: Path | None = None) -> list[dict[str, Any]]:
    artifacts = sorted(artifacts_root.glob("*/preprocessing.json"))
    if only is not None:
        artifacts = [path for path in artifacts if path.parent.name in only]
    workers = max(1, min(MAX_WORKERS, int(workers)))
    contexts = _manifest_contexts(manifest)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(analyze_video, path, output_root, model=model, reasoning_effort=reasoning_effort, curator_context=contexts.get(path.parent.name)) for path in artifacts]
        return [future.result() for future in futures]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, help="preprocessing artifact root")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--only", action="append")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    run(args.artifacts, args.output, model=args.model, reasoning_effort=args.reasoning, workers=args.workers, only=set(args.only) if args.only else None, manifest=args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
