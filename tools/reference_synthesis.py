"""Research-only comparison and editorial-practice synthesis.

This module is deliberately not part of the production editorial pipeline.  It
turns the durable reference-study artifacts into two resumable, schema-bound
hosted requests: one finished-video/VOD comparison per relation, followed by a
single practical ruleset for the application.
"""

from __future__ import annotations

import argparse
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

MAX_WORKERS = 2
COMPARISON_VERSION = 1
SYNTHESIS_VERSION = 2
COMPARISON_PROMPT_VERSION = "reference-finished-vod-comparison-v1"
SYNTHESIS_PROMPT_VERSION = "reference-editorial-practices-v2"
REVIEW_PROMPT_VERSION = "reference-editorial-practices-review-v1"

COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "comparison_summary": {"type": "string"},
        "finished_structure": {"type": "array", "items": {"type": "string"}},
        "vod_to_finished_transformations": {"type": "array", "items": {"type": "string"}},
        "narration_findings": {"type": "array", "items": {"type": "string"}},
        "visual_findings": {"type": "array", "items": {"type": "string"}},
        "opening_consistency": {"type": "string"},
        "editorial_principles": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["comparison_summary", "finished_structure", "vod_to_finished_transformations", "narration_findings", "visual_findings", "opening_consistency", "editorial_principles", "uncertainties"],
    "additionalProperties": False,
}

RULESET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "style_selection_rules": {"type": "array", "items": {"type": "string"}},
        "opening_rules": {"type": "array", "items": {"type": "string"}},
        "narration_rules": {"type": "array", "items": {"type": "string"}},
        "gameplay_editing_rules": {"type": "array", "items": {"type": "string"}},
        "story_and_pacing_rules": {"type": "array", "items": {"type": "string"}},
        "application_guidance": {"type": "array", "items": {"type": "string"}},
        "anti_patterns": {"type": "array", "items": {"type": "string"}},
        "evidence_basis": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "style_selection_rules", "opening_rules", "narration_rules", "gameplay_editing_rules", "story_and_pacing_rules", "application_guidance", "anti_patterns", "evidence_basis", "uncertainties"],
    "additionalProperties": False,
}

COMPARISON_SYSTEM = """You are a careful editor studying a finished gaming video against its source VOD.
Use the supplied finished analysis, VOD analysis, deterministic transcript alignment, and curator notes.
Curator notes are ground truth labels for the study; do not overturn them because a transcript is ambiguous.
Distinguish live streamer commentary from post-recorded narration. Describe observable transformations,
including omitted material, reordered/connected material, opening style, pacing, and visual selection.
Do not judge whether the game run succeeded or failed unless that is explicitly supported. Do not propose
edits for the user's project. Prefer a few concrete findings over generic praise. Return only JSON."""

SYNTHESIS_SYSTEM = """You are the senior editor turning a small, curator-labelled study of gaming videos into
practical guidance for a long-stream editorial assistant. Use all finished-video analyses and finished/VOD
comparisons. Curator notes are ground truth. Separate live commentary from post-recorded narration. The goal
is a good, coherent video, not mechanically hitting a duration target. Treat opening style as a production
contract: a cold open, narrated setup, or gameplay-first opening should be selected deliberately and later
narration should not appear arbitrarily when the established style makes it jarring. Account for initial
playthrough versus challenge-run intent. Avoid hard-coded game-success assumptions. Produce concise rules that
can guide broad story beats and finer actions without pretending the examples cover every game. Return only JSON."""

REVIEW_SYSTEM = """You are the final senior-editor reviewer for a reference study. Critically revise the draft
ruleset against the supplied evidence. Remove claims the examples do not establish, preserve distinctions between
challenge runs and first looks, and make the guidance decisive enough for an editorial model to follow. Maintain
the deliberate opening-style contract and distinguish live commentary from post-recorded narration. Return one
complete replacement ruleset matching the requested schema, not review notes."""


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


def _load(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _response_object(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if payload.get("status") == "incomplete":
        raise SubtitlerError(f"{label} returned incomplete output")
    chunks: list[str] = []
    for output in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) if isinstance(output.get("content"), list) else []:
            if isinstance(content, dict) and content.get("type") == "refusal":
                raise SubtitlerError(f"{label} was refused")
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    try:
        result = json.loads("".join(chunks).strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError(f"{label} returned malformed JSON") from exc
    if not isinstance(result, dict):
        raise SubtitlerError(f"{label} must return a JSON object")
    return result


def _validate(value: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    required = schema.get("required", [])
    if not all(key in value for key in required):
        missing = [key for key in required if key not in value]
        raise SubtitlerError(f"{label} omitted required fields: {', '.join(missing)}")
    if schema.get("additionalProperties") is False and any(key not in schema.get("properties", {}) for key in value):
        raise SubtitlerError(f"{label} returned an unexpected field")
    for key, spec in schema.get("properties", {}).items():
        if key not in value:
            continue
        expected = spec.get("type")
        actual = value[key]
        if expected == "string" and not isinstance(actual, str):
            raise SubtitlerError(f"{label}.{key} must be a string")
        if expected == "array" and (not isinstance(actual, list) or not all(isinstance(item, str) for item in actual)):
            raise SubtitlerError(f"{label}.{key} must be an array of strings")


def _usage_record(payload: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": token_cost(provider, model, input_tokens=input_tokens, output_tokens=output_tokens)}


def _request(*, system: str, prompt: str, schema: dict[str, Any], name: str, model: str, reasoning_effort: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = request_json(
        "POST", "https://api.openai.com/v1/responses",
        {"model": model, "instructions": system, "reasoning": {"effort": reasoning_effort}, "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}], "max_output_tokens": 12_000, "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}},
        ModelLoadError, f"OpenAI {name} request failed",
        headers={"Authorization": f"Bearer {require_api_key('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        timeout_sec=900.0,
    )
    return _response_object(payload, name), payload


def _manifest_items(manifest: Path) -> dict[str, dict[str, Any]]:
    payload = _load(manifest, {})
    rows = payload.get("items", payload.get("videos", [])) if isinstance(payload, dict) else []
    return {str(row.get("key") or row.get("id")): row for row in rows if isinstance(row, dict) and (row.get("key") or row.get("id"))}


def _complete_artifact(path: Path, label: str) -> dict[str, Any]:
    value = _load(path, {}) or {}
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise SubtitlerError(f"{label} is not complete: {path}")
    return value


def _alignment_context(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "alignment_version",
            "finished_utterances",
            "matched_utterances",
            "finished_speech_ms",
            "matched_speech_ms",
            "matched_speech_ratio",
            "finished_duration_ms",
            "vod_duration_ms",
            "source_to_finished_duration_ratio",
            "spans",
        )
    }


def compare_relation(
    relation: dict[str, Any], *, artifacts_root: Path, alignment_root: Path, output_root: Path,
    model: str = "gpt-5.6-luna", reasoning_effort: str = "medium",
) -> dict[str, Any]:
    finished_key = str(relation["finished_key"])
    vod_keys = [str(item) for item in relation.get("vod_keys", [])]
    finished = _complete_artifact(
        artifacts_root / finished_key / "reference-analysis.json", "Finished analysis"
    )
    vods = {
        key: _complete_artifact(
            artifacts_root / key / "reference-analysis.json", f"VOD analysis {key}"
        )
        for key in vod_keys
    }
    alignment = _complete_artifact(
        alignment_root / finished_key / "finished-vod-alignment.json",
        "Finished/VOD alignment",
    )
    alignment = _alignment_context(alignment)
    source = {"relation": relation, "finished": finished, "vods": vods, "alignment": alignment}
    identity = _identity({"version": COMPARISON_VERSION, "prompt": COMPARISON_PROMPT_VERSION, "model": model, "reasoning": reasoning_effort, "source": source})
    out = output_root / finished_key
    result_path = out / "finished-vod-comparison.json"
    cached = _load(result_path)
    if isinstance(cached, dict) and cached.get("cache_identity") == identity and cached.get("status") == "complete":
        return cached
    prompt = "Study this finished/VOD pair. Curator relation and notes are authoritative labels.\n" + json.dumps(source, ensure_ascii=False, indent=2)
    _atomic_json(out / "finished-vod-comparison.request.json", {"model": model, "reasoning_effort": reasoning_effort, "prompt_version": COMPARISON_PROMPT_VERSION, "cache_identity": identity, "prompt": prompt})
    answer, raw = _request(system=COMPARISON_SYSTEM, prompt=prompt, schema=COMPARISON_SCHEMA, name="reference_finished_vod_comparison", model=model, reasoning_effort=reasoning_effort)
    _validate(answer, COMPARISON_SCHEMA, "finished-vod comparison")
    record = {"schema_version": COMPARISON_VERSION, "status": "complete", "finished_key": finished_key, "vod_keys": vod_keys, "prompt_version": COMPARISON_PROMPT_VERSION, "cache_identity": identity, "model": model, "reasoning_effort": reasoning_effort, "usage": _usage_record(raw, "openai", model), "result": answer}
    _atomic_json(result_path, record)
    return record


def compare_manifest(manifest: Path, artifacts_root: Path, alignment_root: Path, output_root: Path, *, model: str = "gpt-5.6-luna", reasoning_effort: str = "medium", workers: int = 1, only: set[str] | None = None) -> list[dict[str, Any]]:
    items = _manifest_items(manifest)
    relations: list[dict[str, Any]] = []
    for key, item in items.items():
        if item.get("kind") != "finished":
            continue
        vod_keys = item.get("vod_keys") or ([item["vod_key"]] if item.get("vod_key") else [])
        if not vod_keys:
            continue
        if only and key not in only:
            continue
        relations.append({"finished_key": key, "vod_keys": list(vod_keys), "creator": item.get("creator"), "notes": item.get("notes", "")})
    workers = max(1, min(MAX_WORKERS, int(workers)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(compare_relation, relation, artifacts_root=artifacts_root, alignment_root=alignment_root, output_root=output_root, model=model, reasoning_effort=reasoning_effort) for relation in relations]
        return [future.result() for future in futures]


def synthesize_ruleset(manifest: Path, analysis_root: Path, output_path: Path, *, model: str = "gpt-5.6-terra", reasoning_effort: str = "medium", final_review_model: str | None = "gpt-5.6-terra", final_review_reasoning: str | None = "high") -> dict[str, Any]:
    items = _manifest_items(manifest)
    finished = []
    comparisons = []
    for key, item in items.items():
        if item.get("kind") != "finished":
            continue
        finished.append(
            _complete_artifact(
                analysis_root / key / "reference-analysis.json", f"Finished analysis {key}"
            )
            | {"key": key, "curator": item}
        )
        if item.get("vod_key", item.get("vod_keys")):
            comparisons.append(
                _complete_artifact(
                    analysis_root / key / "finished-vod-comparison.json",
                    f"Finished/VOD comparison {key}",
                )
                | {"finished_key": key}
            )
    evidence = {"finished": finished, "comparisons": comparisons}
    draft_identity = _identity({"version": SYNTHESIS_VERSION, "prompt": SYNTHESIS_PROMPT_VERSION, "model": model, "reasoning": reasoning_effort, "evidence": evidence})
    prompt = "Synthesize an app-facing editorial practices ruleset from this study. Keep evidence-specific distinctions and do not invent certainty.\n" + json.dumps(evidence, ensure_ascii=False, indent=2)
    draft_path = output_path.with_name(output_path.stem + ".draft.json")
    draft_record = _load(draft_path)
    if not isinstance(draft_record, dict) or draft_record.get("cache_identity") != draft_identity or draft_record.get("status") != "complete":
        _atomic_json(output_path.with_name(output_path.stem + ".draft.request.json"), {"model": model, "reasoning_effort": reasoning_effort, "prompt_version": SYNTHESIS_PROMPT_VERSION, "cache_identity": draft_identity, "prompt": prompt})
        draft, raw = _request(system=SYNTHESIS_SYSTEM, prompt=prompt, schema=RULESET_SCHEMA, name="reference_editorial_practices_draft", model=model, reasoning_effort=reasoning_effort)
        _validate(draft, RULESET_SCHEMA, "editorial-practices draft")
        draft_record = {"schema_version": SYNTHESIS_VERSION, "status": "complete", "prompt_version": SYNTHESIS_PROMPT_VERSION, "cache_identity": draft_identity, "model": model, "reasoning_effort": reasoning_effort, "usage": _usage_record(raw, "openai", model), "result": draft}
        _atomic_json(draft_path, draft_record)
    review_identity = _identity({"version": SYNTHESIS_VERSION, "prompt": REVIEW_PROMPT_VERSION, "draft": draft_identity, "model": final_review_model, "reasoning": final_review_reasoning})
    cached = _load(output_path)
    if isinstance(cached, dict) and cached.get("cache_identity") == review_identity and cached.get("status") == "complete":
        return cached
    if final_review_model and final_review_reasoning:
        review_prompt = "Review and replace this draft using the compact evidence bundle.\nDRAFT:\n" + json.dumps(draft_record["result"], ensure_ascii=False, indent=2) + "\nEVIDENCE:\n" + json.dumps(evidence, ensure_ascii=False)
        _atomic_json(output_path.with_name(output_path.stem + ".review.request.json"), {"model": final_review_model, "reasoning_effort": final_review_reasoning, "prompt_version": REVIEW_PROMPT_VERSION, "cache_identity": review_identity, "prompt": review_prompt})
        answer, review_raw = _request(system=REVIEW_SYSTEM, prompt=review_prompt, schema=RULESET_SCHEMA, name="reference_editorial_practices_review", model=final_review_model, reasoning_effort=final_review_reasoning)
        _validate(answer, RULESET_SCHEMA, "editorial-practices review")
        review_usage = _usage_record(review_raw, "openai", final_review_model)
    else:
        answer = dict(draft_record["result"])
        review_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    draft_usage = dict(draft_record["usage"])
    record = {"schema_version": SYNTHESIS_VERSION, "status": "complete", "prompt_version": REVIEW_PROMPT_VERSION, "cache_identity": review_identity, "model": model, "reasoning_effort": reasoning_effort, "final_review_model": final_review_model, "final_review_reasoning": final_review_reasoning, "draft_usage": draft_usage, "review_usage": review_usage, "cost_usd": float(draft_usage.get("cost_usd", 0.0)) + float(review_usage.get("cost_usd", 0.0)), "result": answer}
    _atomic_json(output_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("manifest", type=Path)
    compare.add_argument("artifacts", type=Path)
    compare.add_argument("alignment", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--model", default="gpt-5.6-luna")
    compare.add_argument("--reasoning", default="medium")
    compare.add_argument("--workers", type=int, default=1)
    compare.add_argument("--only", action="append")
    synth = sub.add_parser("synthesize")
    synth.add_argument("manifest", type=Path)
    synth.add_argument("artifacts", type=Path)
    synth.add_argument("--output", type=Path, required=True)
    synth.add_argument("--model", default="gpt-5.6-terra")
    synth.add_argument("--reasoning", default="medium")
    synth.add_argument("--final-review-model", default="gpt-5.6-terra")
    synth.add_argument("--final-review-reasoning", default="high")
    synth.add_argument("--no-final-review", action="store_true")
    args = parser.parse_args()
    if args.command == "compare":
        compare_manifest(args.manifest, args.artifacts, args.alignment, args.output, model=args.model, reasoning_effort=args.reasoning, workers=args.workers, only=set(args.only) if args.only else None)
    else:
        synthesize_ruleset(args.manifest, args.artifacts, args.output, model=args.model, reasoning_effort=args.reasoning, final_review_model=None if args.no_final_review else args.final_review_model, final_review_reasoning=None if args.no_final_review else args.final_review_reasoning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
