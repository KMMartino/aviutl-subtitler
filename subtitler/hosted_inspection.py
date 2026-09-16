"""Inspectable hosted evidence requests without automatic paid retries."""

from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Sequence

from .api_usage import ApiUsageLedger
from .errors import SubtitlerError
from .external_transcribers import require_api_key
from .hosted_http import request_json


# Standard USD / million tokens, verified September 2026. Never guess unknown prices.
_PRICES = {"gpt-5.6-luna": (0.2, 1.2), "gpt-5.6-terra": (2.0, 12.0), "gpt-5.6-sol": (4.0, 20.0)}
_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf",
                "description", "title", "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"}


def _check_schema(schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS:
        raise SubtitlerError("Inspection schema contains unsupported keywords")
    if "anyOf" in schema:
        for child in schema["anyOf"]:
            _check_schema(child)
    elif schema.get("type") not in ("object", "array", "string", "integer", "number", "boolean", "null"):
        raise SubtitlerError("Inspection schema needs an explicit supported type")
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            raise SubtitlerError("Inspection object schemas must forbid additional properties")
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) != set(properties):
            raise SubtitlerError("Inspection object schemas must require every property")
        for child in properties.values():
            _check_schema(child)
    if schema.get("type") == "array":
        _check_schema(schema.get("items", {}))


def _validate(value: Any, schema: dict[str, Any]) -> bool:
    if "anyOf" in schema and not any(_validate(value, child) for child in schema["anyOf"]):
        return False
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool,
             "null": value is None}
    if kind and not valid.get(kind, False):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if kind == "object":
        props = schema.get("properties", {})
        return set(value) == set(props) and all(_validate(value[k], v) for k, v in props.items())
    if kind == "array" and not all(_validate(item, schema["items"]) for item in value):
        return False
    if kind in ("array", "string"):
        suffix = "Items" if kind == "array" else "Length"
        if not schema.get("min" + suffix, 0) <= len(value) <= schema.get("max" + suffix, math.inf):
            return False
    if kind in ("integer", "number"):
        return math.isfinite(value) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
    return True


class HostedInspectionProvider:
    def __init__(self, usage: ApiUsageLedger, diagnostics_dir: Path, ffmpeg: str = "ffmpeg"):
        self.usage = usage
        self.diagnostics_dir = diagnostics_dir
        self.ffmpeg = ffmpeg
        self._lock = threading.Lock()

    def inspect(self, *, operation: str, model: str, prompt: str, schema: dict[str, Any],
                images: Sequence[Path] = (), max_output_tokens: int = 6000,
                reasoning_effort: str = "medium") -> dict[str, Any]:
        if model not in _PRICES:
            raise SubtitlerError("Inspection model has no approved price")
        if reasoning_effort not in ("none", "minimal", "low", "medium"):
            raise SubtitlerError("Inspection reasoning effort must not exceed medium")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32000 or len(images) > 64:
            raise SubtitlerError("Inspection request exceeds token or image limits")
        _check_schema(schema)
        if schema.get("type") != "object":
            raise SubtitlerError("Inspection response schema must be an object")
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for index, path in enumerate(images, 1):
            suffix = path.suffix.lower()
            if suffix not in (".jpg", ".jpeg", ".png", ".webp") or path.stat().st_size > 20_000_000:
                raise SubtitlerError("Inspection images must be JPEG, PNG or WebP under 20 MB")
            mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/" + suffix[1:]
            stamp = re.fullmatch(r"frame-(\d+)", path.stem)
            label = f"Image {index}"
            if stamp:
                label += f"; original source timestamp {int(stamp[1])} milliseconds"
            content.append({"type": "input_text", "text": label + "."})
            content.append({"type": "input_image", "detail": "high", "image_url":
                            f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")})
        # UTF-8 bytes bound text tokens; high-detail image allowance intentionally exceeds
        # ordinary 1920px frame costs. 2.5x input / 1.5x output cover long context and writes.
        input_ceiling = len(prompt.encode("utf-8")) + len(json.dumps(schema).encode("utf-8")) + 2048 + 16384 * len(images)
        input_rate, output_rate = _PRICES[model]
        ceiling = (input_ceiling * input_rate * 2.5 + max_output_tokens * output_rate * 1.5) / 1_000_000
        payload = {"model": model, "input": [{"role": "user", "content": content}],
                   "reasoning": {"effort": reasoning_effort}, "max_output_tokens": max_output_tokens,
                   "text": {"format": {"type": "json_schema", "name": "inspection", "strict": True, "schema": schema}}}
        with self._lock:
            api_key = require_api_key("OPENAI_API_KEY")
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^a-zA-Z0-9_-]", "_", operation)[:60] or "inspection"
            diagnostic = self.diagnostics_dir / f"{name}-{uuid.uuid4().hex}.json"
            record: dict[str, Any] = {"operation": operation, "model": model, "prompt": prompt,
                                     "schema": schema, "images": [str(p) for p in images],
                                     "max_output_tokens": max_output_tokens, "reasoning_effort": reasoning_effort,
                                     "reserved_cost_usd": ceiling, "status": "pending"}
            def persist() -> None:
                diagnostic.write_text(json.dumps(record, ensure_ascii=False, indent=2).replace(api_key, "[REDACTED]"), encoding="utf-8")
            def charge_unconfirmed() -> None:
                self.usage.add(provider="openai", model=model, operation=operation + ":unconfirmed_estimate",
                               cost_usd=ceiling)
                record["estimated_cost_usd"] = ceiling
                record["cost_is_estimate"] = True
            persist()
            try:
                data = request_json("POST", "https://api.openai.com/v1/responses", payload,
                                    SubtitlerError, "Hosted inspection failed",
                                    headers={"Authorization": f"Bearer {api_key}"}, attempts=1)
            except Exception:
                charge_unconfirmed()
                record["status"] = "request_failed_usage_unknown"
                persist()
                raise
            record["response"] = data
            usage = data.get("usage")
            if not isinstance(usage, dict) or not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
                charge_unconfirmed()
                record["status"] = "usage_unknown"
                persist()
                raise SubtitlerError("Inspection response omitted usage; its budget reservation remains charged")
            inputs, outputs = usage["input_tokens"], usage["output_tokens"]
            # Responses output_tokens includes hidden reasoning. Do not add it twice.
            cost = (inputs * input_rate + outputs * output_rate) / 1_000_000
            if inputs > 272000:
                cost = (inputs * input_rate * 2 + outputs * output_rate * 1.5) / 1_000_000
            details = usage.get("input_tokens_details")
            writes = details.get("cache_write_tokens", details.get("cache_creation_tokens", 0)) if isinstance(details, dict) else 0
            if isinstance(writes, int) and writes > 0:
                cost += writes * input_rate * 0.25 * (2 if inputs > 272000 else 1) / 1_000_000
            cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
            if isinstance(cached, int) and 0 < cached <= inputs:
                cost -= cached * input_rate * 0.9 * (2 if inputs > 272000 else 1) / 1_000_000
            self.usage.add(provider="openai", model=model, operation=operation,
                           input_tokens=inputs, output_tokens=outputs, cost_usd=cost)
            record["cost_usd"] = cost
            try:
                if data.get("status") != "completed":
                    raise ValueError("response did not complete")
                text = "".join(part.get("text", "") for item in data.get("output", [])
                               if item.get("type") == "message" for part in item.get("content", [])
                               if part.get("type") == "output_text")
                result = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(result, dict) or not _validate(result, schema):
                    raise ValueError("response did not match schema")
            except (ValueError, TypeError, AttributeError) as exc:
                record["status"] = "invalid_response"
                persist()
                raise SubtitlerError("Inspection returned incomplete or invalid structured output") from exc
            record["status"] = "complete"
            persist()
            return result


def extract_inspection_frames(media_path: Path, timestamps_ms: Sequence[int], directory: Path,
                              ffmpeg: str = "ffmpeg") -> list[Path]:
    """Extract readable frames; caller owns source-fingerprint isolation of directory."""
    if len(timestamps_ms) > 64 or any(type(t) is not int or t < 0 for t in timestamps_ms):
        raise SubtitlerError("Inspection requires at most 64 nonnegative integer timestamps")
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for timestamp in timestamps_ms:
        target = directory / f"frame-{timestamp:012d}.jpg"
        pending = directory / f"frame-{timestamp:012d}-{uuid.uuid4().hex}.jpg"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp / 1000:.3f}",
                   "-i", str(media_path), "-frames:v", "1", "-vf",
                   "scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease",
                   "-q:v", "2", str(pending)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=90, check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            pending.unlink(missing_ok=True)
            raise SubtitlerError("Could not extract inspection frame") from exc
        if result.returncode or not pending.is_file() or not pending.stat().st_size:
            pending.unlink(missing_ok=True)
            raise SubtitlerError(f"Could not extract inspection frame at {timestamp}ms")
        pending.replace(target)
        frames.append(target)
    return frames
