"""Checkpointable evidence lookup for reference-dependent editorial suggestions."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .api_costs import token_cost
from .errors import ModelLoadError, SubtitlerError
from .external_transcribers import require_api_key
from .hosted_http import request_json
from .media_analysis import VisualSample, _extract_samples


ASSET_PROMPT_VERSION = "editorial-assets-v1"
MAX_EVIDENCE_REQUESTS = 16
MAX_CANDIDATES_PER_REQUEST = 8


class EditorialEvidenceProvider(Protocol):
    model: str

    def select_reference(
        self, prompt: str, samples: Sequence[VisualSample], labels: Sequence[str]
    ) -> tuple[dict[str, Any], int, int]: ...


@dataclass(frozen=True)
class OpenAIEditorialEvidenceProvider:
    model: str
    api_key: str

    @classmethod
    def from_environment(cls, model: str) -> OpenAIEditorialEvidenceProvider:
        return cls(model=model, api_key=require_api_key("OPENAI_API_KEY"))

    def select_reference(
        self, prompt: str, samples: Sequence[VisualSample], labels: Sequence[str]
    ) -> tuple[dict[str, Any], int, int]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for sample, label in zip(samples, labels):
            encoded = base64.b64encode(sample.jpeg_path.read_bytes()).decode("ascii")
            content.extend(
                (
                    {"type": "text", "text": label},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
                    },
                )
            )
        schema = {
            "type": "object",
            "properties": {
                "candidate_index": {"type": "integer"},
                "verified": {"type": "boolean"},
                "caption": {"type": "string"},
                "verification_note": {"type": "string"},
                "confidence": {"type": "number"},
                "crop_x": {"type": "number"},
                "crop_y": {"type": "number"},
                "crop_width": {"type": "number"},
                "crop_height": {"type": "number"},
            },
            "required": [
                "candidate_index", "verified", "caption", "verification_note", "confidence",
                "crop_x", "crop_y", "crop_width", "crop_height",
            ],
            "additionalProperties": False,
        }
        data = request_json(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You verify visual evidence for a video editor. Use only visible evidence and return the requested JSON.",
                    },
                    {"role": "user", "content": content},
                ],
                "reasoning_effort": "low",
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "editorial_reference", "strict": True, "schema": schema},
                },
                "max_completion_tokens": 2048,
            },
            ModelLoadError,
            "OpenAI editorial evidence selection failed",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout_sec=300.0,
        )
        choices = data.get("choices") if isinstance(data.get("choices"), list) else []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        try:
            parsed = json.loads(str(text))
        except json.JSONDecodeError as exc:
            raise SubtitlerError("Editorial evidence selection returned malformed JSON") from exc
        if not isinstance(parsed, dict):
            raise SubtitlerError("Editorial evidence selection returned no object")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return parsed, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def resolve_editorial_assets(
    project: dict[str, Any],
    *,
    workspace: Path,
    provider: EditorialEvidenceProvider,
    output_locale: str = "en",
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    """Find and verify source frames only for selected edits that request evidence."""
    workspace.mkdir(parents=True, exist_ok=True)
    editorial_map = project.get("editorial_map", {})
    edits = [dict(item) for item in editorial_map.get("supporting_edits", []) if isinstance(item, dict)]
    sources = {
        str(item.get("source_id")): item
        for item in project.get("sources", []) if isinstance(item, dict)
    }
    assets: list[dict[str, Any]] = []
    input_tokens = 0
    output_tokens = 0
    requests = [item for item in edits if item.get("evidence_request")][:MAX_EVIDENCE_REQUESTS]
    for request_index, edit in enumerate(requests):
        candidates = _candidate_timestamps(edit, sources)
        if not candidates:
            _mark_unverified(edit, "No candidate source range was available for this reference.")
            continue
        request_dir = workspace / str(edit.get("edit_id") or f"request-{request_index + 1:03d}")
        request_dir.mkdir(parents=True, exist_ok=True)
        samples: list[VisualSample] = []
        labels: list[str] = []
        candidate_records: list[tuple[str, int]] = []
        for source_id, timestamp_ms in candidates:
            source = sources[source_id]
            extracted = _extract_samples(
                Path(str(source["visual_path"])),
                media_kind="video",
                timestamps=[timestamp_ms / 1000.0],
                ffmpeg=ffmpeg,
                output_dir=request_dir,
                prefix=f"candidate-{len(samples):02d}",
            )[0]
            samples.append(VisualSample(len(samples), extracted.timestamp_sec, extracted.jpeg_path))
            labels.append(
                f"Candidate {len(samples) - 1}: {source.get('original_name')} at {timestamp_ms} ms."
            )
            candidate_records.append((source_id, timestamp_ms))
        prompt = _evidence_prompt(edit, output_locale)
        parsed, used_input, used_output = provider.select_reference(prompt, samples, labels)
        input_tokens += used_input
        output_tokens += used_output
        selected_index = _bounded_int(parsed.get("candidate_index"), 0, len(samples) - 1)
        verified = bool(parsed.get("verified"))
        source_id, timestamp_ms = candidate_records[selected_index]
        selected_path = samples[selected_index].jpeg_path
        crop = _normalized_crop(parsed)
        asset_path = request_dir / "selected.jpg"
        _write_crop(selected_path, asset_path, crop, ffmpeg)
        asset = {
            "asset_id": f"asset-{len(assets) + 1:03d}",
            "edit_id": edit.get("edit_id"),
            "source_id": source_id,
            "timestamp_ms": timestamp_ms,
            "path": str(asset_path),
            "caption": str(parsed.get("caption") or "").strip()[:1000],
            "verification_note": str(parsed.get("verification_note") or "").strip()[:1000],
            "verified": verified,
            "confidence": _unit(parsed.get("confidence")),
            "crop": crop,
        }
        assets.append(asset)
        edit["resolved_asset_id"] = asset["asset_id"]
        edit["evidence_verified"] = verified
        if not verified:
            _mark_unverified(edit, asset["verification_note"] or "The requested visual was not verified.")
    edits_by_id = {str(item.get("edit_id")): item for item in edits}
    cost = token_cost(
        "openai", provider.model, input_tokens=input_tokens, output_tokens=output_tokens
    )
    return {
        "prompt_version": ASSET_PROMPT_VERSION,
        "supporting_edits": [edits_by_id.get(str(item.get("edit_id")), item) for item in editorial_map.get("supporting_edits", []) if isinstance(item, dict)],
        "editorial_assets": assets,
        "api_cost_usd": cost,
        "api_usage": [{
            "provider": "openai",
            "model": provider.model,
            "operation": "editorial_assets",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": cost,
        }] if input_tokens or output_tokens else [],
    }


def _candidate_timestamps(
    edit: dict[str, Any], sources: dict[str, dict[str, Any]]
) -> list[tuple[str, int]]:
    query_tokens = set(re.findall(r"[\w\u3040-\u30ff\u3400-\u9fff]+", str(edit.get("reference_query") or "").casefold()))
    requested = [str(value) for value in edit.get("reference_source_ids", []) if str(value) in sources]
    source_ids = requested or list(sources)
    scored: list[tuple[float, str, int]] = []
    for source_id in source_ids:
        source = sources[source_id]
        visual = source.get("stages", {}).get("visual_learning", {}).get("output")
        segments = visual.get("segments", []) if isinstance(visual, dict) else []
        for segment in segments if isinstance(segments, list) else []:
            if not isinstance(segment, dict):
                continue
            start_ms = max(0, int(segment.get("start_ms", 0)))
            end_ms = min(int(source.get("duration_ms", 0)), int(segment.get("end_ms", start_ms + 1)))
            if end_ms <= start_ms:
                continue
            haystack = " ".join(
                [str(segment.get("description") or ""), *[str(value) for value in segment.get("tags", [])]]
            ).casefold()
            score = sum(1.0 for token in query_tokens if token in haystack)
            score += max(0.0, float(segment.get("confidence", 0.0))) * 0.2
            for fraction in (0.2, 0.5, 0.8):
                timestamp = round(start_ms + (end_ms - start_ms) * fraction)
                scored.append((score, source_id, timestamp))
    if not scored:
        for source_id in source_ids:
            duration = int(sources[source_id].get("duration_ms", 0))
            for fraction in (0.2, 0.5, 0.8):
                scored.append((0.0, source_id, round(duration * fraction)))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    result: list[tuple[str, int]] = []
    for _, source_id, timestamp in scored:
        if any(existing_id == source_id and abs(existing_ms - timestamp) < 3000 for existing_id, existing_ms in result):
            continue
        result.append((source_id, timestamp))
        if len(result) >= MAX_CANDIDATES_PER_REQUEST:
            break
    return result


def _evidence_prompt(edit: dict[str, Any], locale: str) -> str:
    language = "Japanese" if locale == "ja" else "English"
    return f"""Select the candidate frame that best supports this proposed editorial reference.
Suggestion: {edit.get('instruction', '')}
What to find: {edit.get('reference_query', '')}

Rules:
- Verify only what is visibly supported. A plausible frame is not proof.
- If none supports the requested fact, set verified=false and explain briefly.
- Select a tight normalized crop (x, y, width, height from 0.0 to 1.0) around the useful evidence. Use the full frame when cropping would remove context.
- Write caption and verification_note in {language}.
"""


def _normalized_crop(value: dict[str, Any]) -> dict[str, float]:
    x = _unit(value.get("crop_x"))
    y = _unit(value.get("crop_y"))
    width = max(0.05, min(1.0 - x, _unit(value.get("crop_width")) or 1.0))
    height = max(0.05, min(1.0 - y, _unit(value.get("crop_height")) or 1.0))
    return {"x": x, "y": y, "width": width, "height": height}


def _write_crop(source: Path, target: Path, crop: dict[str, float], ffmpeg: str) -> None:
    if crop == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}:
        shutil.copyfile(source, target)
        return
    expression = (
        f"crop=iw*{crop['width']:.6f}:ih*{crop['height']:.6f}:"
        f"iw*{crop['x']:.6f}:ih*{crop['y']:.6f}"
    )
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source), "-vf", expression, "-frames:v", "1", "-y", str(target)],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0 or not target.is_file():
        shutil.copyfile(source, target)


def _mark_unverified(edit: dict[str, Any], note: str) -> None:
    edit["evidence_verified"] = False
    edit["verification_note"] = note[:1000]
    instruction = str(edit.get("instruction") or "").strip()
    edit["instruction"] = f"Verify manually before using this idea: {instruction}" if instruction else "Verify this reference manually."


def _bounded_int(value: Any, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = lower
    return max(lower, min(upper, number))


def _unit(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
