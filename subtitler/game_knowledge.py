"""Bounded, persistent game knowledge shared by editorial projects."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .errors import SubtitlerError
from .editorial_locale import editorial_locale, output_language_instruction


GAME_KNOWLEDGE_SCHEMA_VERSION = 2
GAME_KNOWLEDGE_FIELDS = (
    "visual_signatures",
    "locations",
    "bosses_enemies",
    "menus_and_upgrade_states",
    "retry_patterns",
    "objectives_and_mechanics",
    "progress_and_result_cues",
    "terminology",
)
MAX_GAMES = 80
MAX_ITEMS_PER_FIELD = 16
MAX_ITEM_LENGTH = 240
GAME_KNOWLEDGE_MAX_OUTPUT_TOKENS = 8_192
GAME_KNOWLEDGE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        field: {"type": "array", "items": {"type": "string"}}
        for field in GAME_KNOWLEDGE_FIELDS
    },
    "required": list(GAME_KNOWLEDGE_FIELDS),
    "additionalProperties": False,
}


class StructuredProvider(Protocol):
    def complete_structured(
        self,
        prompt: str,
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...


def normalize_game_key(title: str) -> str:
    return " ".join(title.casefold().split())


def load_game_profile(path: Path | None, title: str) -> dict[str, Any]:
    if path is None or not title.strip():
        return _empty_profile(title)
    store = _load_store(path)
    key = normalize_game_key(title)
    for profile in store["games"]:
        if isinstance(profile, dict) and profile.get("key") == key:
            return _normalized_profile(profile, title)
    return _empty_profile(title)


def update_game_profile(
    *,
    path: Path | None,
    title: str,
    provider: StructuredProvider,
    visual_summary: dict[str, Any],
    transcript_excerpt: list[dict[str, Any]],
    temporal_bursts: list[dict[str, Any]],
    reference_context: dict[str, Any] | None = None,
    output_locale: str = "en",
) -> dict[str, Any]:
    """Learn durable game-specific cues without letting context grow without bound."""
    current = load_game_profile(path, title)
    prompt = f"""Task: maintain a compact, reusable game knowledge profile for long-form video editing.

Output language: {output_language_instruction(output_locale)}

Game key: {title}
Existing profile:
{json.dumps(current.get('knowledge', {}), ensure_ascii=False, separators=(',', ':'))}

New visual analysis:
{json.dumps(visual_summary, ensure_ascii=False, separators=(',', ':'))[:60000]}

Representative transcript evidence:
{json.dumps(transcript_excerpt, ensure_ascii=False, separators=(',', ':'))[:20000]}

Temporal transition bursts:
{json.dumps(temporal_bursts, ensure_ascii=False, separators=(',', ':'))[:20000]}

Bounded public reference context (may be unavailable or imperfect; prefer recording evidence on conflict):
{json.dumps(reference_context or {}, ensure_ascii=False, separators=(',', ':'))[:6000]}

Rules:
- Merge useful existing knowledge with supported new findings.
- Actively remove or replace existing claims contradicted by stronger new evidence; do not preserve a claim merely because it was already stored.
- Learn recurring HUD/state cues, named places and enemies, retry/reset patterns, menus/upgrades, objectives, mechanics, stable progress/result cues, and terminology.
- Each entry must be a concise standalone fact useful to a later editorial analysis.
- Mark uncertainty in the text instead of presenting guesses as facts.
- Prefer stable, reusable knowledge over a chronological recap of this recording.
- Do not store the outcome of this particular recording as game knowledge. In bosses_enemies, retain stable identity, mechanics, phases, and visible signatures, never whether this run won, lost, survived, or returned to a menu afterward.
- Put stable result-screen semantics in progress_and_result_cues: record what recurring epilogue, statistics, clear, game-over, unlock, credits, or title-return cues mean when combined evidence establishes that meaning. Name the visible or spoken evidence that distinguishes a clear from a failure or an ordinary transition. Keep the general cue; omit what happened in this recording.
- Never infer victory or defeat from empty HUD/party slots, a dark transition, a statistics screen, or a title/menu return alone. Explicit transcript statements and later continuity outweigh an earlier visual guess; omit a disputed outcome instead of choosing one.
- Complete all schema arrays, including progress_and_result_cues. An empty array is correct when no stable cue is established; never fill it with a run-specific guess.
- Return at most {MAX_ITEMS_PER_FIELD} entries per array.

Completion: every existing claim has been retained, revised, or removed based on the combined evidence; return only the JSON object required by the response schema.
"""
    raw = provider.complete_structured(
        prompt,
        max_tokens=GAME_KNOWLEDGE_MAX_OUTPUT_TOKENS,
        operation="editorial_game_learning",
        response_schema=GAME_KNOWLEDGE_RESPONSE_SCHEMA,
    )
    parsed = _json_object(raw)
    knowledge = {
        field: _bounded_strings(parsed.get(field))
        for field in GAME_KNOWLEDGE_FIELDS
    }
    now = datetime.now(timezone.utc).isoformat()
    profile = {
        "key": normalize_game_key(title),
        "title": title.strip(),
        "revision": int(current.get("revision", 0)) + 1,
        "created_at_utc": str(current.get("created_at_utc") or now),
        "updated_at_utc": now,
        "last_used_at_utc": now,
        "knowledge": knowledge,
        "output_locale": editorial_locale(output_locale),
        "reference_context": reference_context or current.get("reference_context", {}),
    }
    if path is not None:
        store = _load_store(path)
        games = [
            item for item in store["games"]
            if isinstance(item, dict) and item.get("key") != profile["key"]
        ]
        games.append(profile)
        games.sort(key=lambda item: str(item.get("last_used_at_utc") or ""), reverse=True)
        store["games"] = games[:MAX_GAMES]
        _write_store(path, store)
    return profile


def game_profile_context(profile: dict[str, Any], *, max_characters: int = 9000) -> str:
    knowledge = profile.get("knowledge") if isinstance(profile.get("knowledge"), dict) else {}
    compact = {field: _bounded_strings(knowledge.get(field)) for field in GAME_KNOWLEDGE_FIELDS}
    reference = profile.get("reference_context") if isinstance(profile.get("reference_context"), dict) else {}
    return json.dumps(
        {"learned": compact, "public_reference": reference},
        ensure_ascii=False,
        separators=(",", ":"),
    )[:max_characters]


def _empty_profile(title: str) -> dict[str, Any]:
    return {
        "key": normalize_game_key(title),
        "title": title.strip(),
        "revision": 0,
        "knowledge": {field: [] for field in GAME_KNOWLEDGE_FIELDS},
        "reference_context": {},
    }


def _normalized_profile(value: dict[str, Any], fallback_title: str) -> dict[str, Any]:
    profile = dict(value)
    profile["key"] = normalize_game_key(str(value.get("title") or fallback_title))
    profile["title"] = str(value.get("title") or fallback_title).strip()
    profile["revision"] = max(0, int(value.get("revision") or 0))
    knowledge = value.get("knowledge") if isinstance(value.get("knowledge"), dict) else {}
    profile["knowledge"] = {field: _bounded_strings(knowledge.get(field)) for field in GAME_KNOWLEDGE_FIELDS}
    profile["reference_context"] = (
        value.get("reference_context") if isinstance(value.get("reference_context"), dict) else {}
    )
    return profile


def _load_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": GAME_KNOWLEDGE_SCHEMA_VERSION, "games": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubtitlerError(f"Could not read editorial game knowledge store {path}: {exc}") from exc
    games = value.get("games") if isinstance(value, dict) else None
    return {
        "schema_version": GAME_KNOWLEDGE_SCHEMA_VERSION,
        "games": games if isinstance(games, list) else [],
    }


def _write_store(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _bounded_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item or "").split())[:MAX_ITEM_LENGTH]
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
        if len(result) >= MAX_ITEMS_PER_FIELD:
            break
    return result


def _json_object(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3]
    try:
        parsed = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Game learning returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise SubtitlerError("Game learning must return a JSON object")
    return parsed
