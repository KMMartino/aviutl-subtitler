"""Hosted, suggestion-only semantic analysis for one long-form source at a time."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .errors import StructuredOutputIncompleteError, SubtitlerError
from .editorial_locale import locale_label, output_language_instruction
from .editorial_practices import EDITORIAL_PRACTICES_POLICY


EDITORIAL_PROMPT_VERSION = "editorial-factual-map-v15"
DEFAULT_WINDOW_MS = 15 * 60 * 1000
DEFAULT_WINDOW_OVERLAP_MS = 90 * 1000
MAX_EDITORIAL_WINDOW_WORKERS = 3
SAFE_BOUNDARY_SEARCH_MS = 2 * 60 * 1000
EDITORIAL_OUTPUT_MAX_TOKENS = 16_384
AUDIO_EDITORIAL_OUTPUT_MAX_TOKENS = 8_192
DIRECTOR_OUTPUT_MAX_TOKENS = 32_768
MIN_OVERFLOW_SPLIT_MS = 5 * 60 * 1000
MAX_OVERFLOW_SPLIT_DEPTH = 3
UTTERANCE_FALLBACK_GAP_MS = 8_000
MAX_CONTINUATION_GAP_MS = 20_000
MAX_EPISODE_UNIT_EXTENSION_MS = 6 * 60 * 1000
ACTIVITY_EPISODE_KINDS = frozenset(
    {
        "setup",
        "activity",
        "attempt",
        "exploration",
        "encounter",
        "management",
        "recovery",
        "interruption",
        "transition",
        "other",
    }
)
CONTEXT_FIELDS = (
    "current_objectives",
    "completed_milestones",
    "open_threads",
    "recurring_locations_entities_mechanics",
    "known_repetition_patterns",
    "creator_stance_and_sentiment",
)


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer"}
_STRING_ARRAY = _array(_STRING)
_TIMED_BASE = {
    "start_ms": _INTEGER,
    "end_ms": _INTEGER,
}


EDITORIAL_MAP_RESPONSE_SCHEMA = _strict_object(
    {
        "summary": _STRING,
        "context_update": _strict_object(
            {field: _STRING_ARRAY for field in CONTEXT_FIELDS}
        ),
        "semantic_spans": _array(
            _strict_object(
                {
                    **_TIMED_BASE,
                    "label": _STRING,
                    "kind": _STRING,
                    "summary": _STRING,
                    "confidence": _NUMBER,
                    "evidence_refs": _STRING_ARRAY,
                }
            )
        ),
    }
)



HUMAN_INFORMATION_SYNTHESIS_SCHEMA = _strict_object(
    {
        "progression_summary": _STRING,
        "event_phases": _array(
            _strict_object(
                {
                    "source_id": _STRING,
                    **_TIMED_BASE,
                    "label": _STRING,
                    "summary": _STRING,
                    "category": _STRING,
                    "thread_ids": _STRING_ARRAY,
                }
            )
        ),
        "story_threads": _array(
            _strict_object(
                {
                    "thread_id": _STRING,
                    "title": _STRING,
                    "summary": _STRING,
                    "category": _STRING,
                    "anchors": _array(
                        _strict_object(
                            {
                                "source_id": _STRING,
                                **_TIMED_BASE,
                                "label": _STRING,
                                "relationship": _STRING,
                            }
                        )
                    ),
                }
            )
        ),
        "narration_briefs": _array(
            _strict_object(
                {
                    "source_id": _STRING,
                    **_TIMED_BASE,
                    "kind": {
                        "type": "string",
                        "enum": [
                            "setup",
                            "mechanic_explanation",
                            "causal_bridge",
                            "retry_compression",
                            "lore_synthesis",
                            "payoff_callback",
                            "content_mediation",
                            "outcome_context",
                        ],
                    },
                    "purpose": _STRING,
                    "memory_jog": _STRING,
                    "talking_points": _STRING_ARRAY,
                    "representative_visuals": _STRING_ARRAY,
                    "thread_ids": _STRING_ARRAY,
                }
            )
        ),
        "uncertainties": _STRING_ARRAY,
    }
)






SELECTIVE_SUBTITLE_RESPONSE_SCHEMA = _strict_object(
    {
        "selected_phrases": _array(
            _strict_object(
                {
                    **_TIMED_BASE,
                    "exact_phrase": _STRING,
                    "reason": _STRING,
                    "emphasis_energy": _NUMBER,
                    "confidence": _NUMBER,
                }
            )
        )
    }
)

ACTIVITY_EPISODE_RESPONSE_SCHEMA = _strict_object(
    {
        "episodes": _array(
            _strict_object(
                {
                    "episode_key": _STRING,
                    "level": {"type": "integer", "enum": [1, 2]},
                    "parent_episode_key": _STRING,
                    "episode_kind": {
                        "type": "string",
                        "enum": sorted(ACTIVITY_EPISODE_KINDS),
                    },
                    **_TIMED_BASE,
                    "label": _STRING,
                    "summary": _STRING,
                    "continuity_key": _STRING,
                    "confidence": _NUMBER,
                }
            )
        )
    }
)


class EditorialPlanningProvider(Protocol):
    def complete_structured(
        self,
        prompt: str,
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class TranscriptEvidence:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class VisualEvidence:
    start_ms: int
    end_ms: int
    description: str
    tags: tuple[str, ...] = ()
    confidence: float = 0.0
    motion_level: float | None = None
    visual_category: str = "other"
    observed_label: str = ""


@dataclass(frozen=True)
class EditorialAnalysisWindow:
    index: int
    start_ms: int
    end_ms: int
    evidence_start_ms: int
    evidence_end_ms: int


def build_utterance_groups(
    transcript: Sequence[TranscriptEvidence],
) -> list[dict[str, Any]]:
    """Join display fragments into sentence-like units of spoken meaning."""
    ordered = sorted(
        (item for item in transcript if item.end_ms > item.start_ms and item.text.strip()),
        key=lambda item: (item.start_ms, item.end_ms),
    )
    groups: list[dict[str, Any]] = []
    for item in ordered:
        if not groups:
            groups.append(
                {
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "text": item.text.strip(),
                    "speech_spans": [{"start_ms": item.start_ms, "end_ms": item.end_ms}],
                }
            )
            continue
        current = groups[-1]
        gap_ms = item.start_ms - int(current["end_ms"])
        terminal = str(current["text"]).rstrip().endswith((".", "?", "!", "。", "？", "！"))
        continuation = str(current["text"]).rstrip().endswith(
            (",", "、", "，", ":", "：", "—", "-", "…")
        )
        continuation_allowed = (
            continuation
            and gap_ms <= MAX_CONTINUATION_GAP_MS
        )
        starts_new_thought = terminal or (
            not continuation_allowed and gap_ms > UTTERANCE_FALLBACK_GAP_MS
        )
        if starts_new_thought:
            groups.append(
                {
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "text": item.text.strip(),
                    "speech_spans": [{"start_ms": item.start_ms, "end_ms": item.end_ms}],
                }
            )
            continue
        current["end_ms"] = max(int(current["end_ms"]), item.end_ms)
        current["text"] = f"{current['text']} {item.text.strip()}"[:4000]
        current["speech_spans"].append(
            {"start_ms": item.start_ms, "end_ms": item.end_ms}
        )
    return groups


def build_editorial_event_graph(
    *,
    source_id: str,
    source_duration_ms: int,
    visuals: Sequence[VisualEvidence],
    semantic_spans: Sequence[dict[str, Any]],
    audio_intents: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Build a local observational timeline without folding interpretation into it."""
    boundaries = {0, source_duration_ms}
    for item in visuals:
        boundaries.update((item.start_ms, item.end_ms))
    ordered = sorted(value for value in boundaries if 0 <= value <= source_duration_ms)
    nodes: list[dict[str, Any]] = []
    for start_ms, end_ms in zip(ordered, ordered[1:]):
        if end_ms <= start_ms:
            continue
        visual = _best_overlap_visual(visuals, start_ms, end_ms)
        visual_category = visual.visual_category if visual else "unknown"
        visual_tags = list(visual.tags) if visual else []
        observed_label = _observed_state_label(visual)
        signature_parts = [
            visual_category.casefold(),
            observed_label.casefold(),
            *sorted(tag.casefold() for tag in visual_tags),
        ]
        signature = "|".join(part for part in signature_parts if part) or "unknown"
        node = {
            "source_id": source_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "observed_label": observed_label,
            "visual_category": visual_category,
            "visual_tags": visual_tags,
            "state_signature": signature,
            "confidence": visual.confidence if visual else 0.0,
            "possible_interruption": False,
        }
        if nodes and _event_nodes_match(nodes[-1], node):
            nodes[-1]["end_ms"] = end_ms
            continue
        nodes.append(node)
    edges: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        node["event_id"] = f"{source_id}:event:{index + 1:04d}"
    for index in range(1, len(nodes)):
        previous = nodes[index - 1]
        current = nodes[index]
        relationship = "continues" if _visual_states_match(previous, current) else "transition"
        edges.append(
            {
                "from_event_id": previous["event_id"],
                "to_event_id": current["event_id"],
                "relationship": relationship,
            }
        )
    state_runs: list[list[dict[str, Any]]] = []
    for node in nodes:
        if state_runs and _visual_states_match(state_runs[-1][-1], node):
            state_runs[-1].append(node)
        else:
            state_runs.append([node])
    for left, middle, right in zip(state_runs, state_runs[1:], state_runs[2:]):
        if not _visual_states_match(left[-1], right[0]):
            continue
        for node in middle:
            node["possible_interruption"] = True
        edges.append(
            {
                "from_event_id": left[-1]["event_id"],
                "to_event_id": right[0]["event_id"],
                "relationship": "returns_to",
                "intervening_event_ids": [node["event_id"] for node in middle],
            }
        )
    return {"nodes": nodes, "edges": edges}


def build_activity_episode_layer(
    *,
    provider: EditorialPlanningProvider,
    source_id: str,
    source_duration_ms: int,
    title_or_game: str,
    objective: str,
    event_graph: dict[str, Any],
    semantic_spans: Sequence[dict[str, Any]],
    analysis_windows: Sequence[EditorialAnalysisWindow],
    output_locale: str,
    max_workers: int = MAX_EDITORIAL_WINDOW_WORKERS,
    progress: Callable[[str], None] | None = None,
    cached_candidates: dict[int, list[dict[str, Any]]] | None = None,
    candidate_completed: Callable[[int, list[dict[str, Any]]], None] | None = None,
    cached_canonical: Sequence[dict[str, Any]] | None = None,
    canonical_completed: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """Create a bounded nested episode layer above the atomic event timeline."""
    nodes = _object_list(event_graph.get("nodes"))
    if cached_canonical is not None:
        episodes = [dict(item) for item in cached_canonical if isinstance(item, dict)]
        for node in nodes:
            node["activity_episode_ids"] = [
                str(item["episode_id"])
                for item in episodes
                if item.get("episode_id") and _items_overlap(node, item)
            ]
        return episodes
    candidate_cache = cached_candidates or {}

    def map_window(window: EditorialAnalysisWindow) -> list[dict[str, Any]]:
        if window.index in candidate_cache:
            return [dict(item) for item in candidate_cache[window.index]]
        local_nodes = [
            _compact_episode_event(item)
            for item in nodes
            if int(item.get("end_ms", 0)) > window.evidence_start_ms
            and int(item.get("start_ms", 0)) < window.evidence_end_ms
        ]
        local_spans = [
            {
                "start_ms": item.get("start_ms"),
                "end_ms": item.get("end_ms"),
                "kind": item.get("kind"),
                "label": item.get("label"),
                "summary": str(item.get("summary") or "")[:500],
            }
            for item in semantic_spans
            if _items_overlap(
                item,
                {"start_ms": window.evidence_start_ms, "end_ms": window.evidence_end_ms},
            )
        ]
        prompt = f"""Task: group one overlapping portion of an atomic video-state timeline into meaningful activity episodes.

Output language: {output_language_instruction(output_locale)}
Title/game: {title_or_game}
Recording objective: {objective}
Source ID: {source_id}
Core processing range: {window.start_ms}-{window.end_ms} ms
Available overlapping evidence: {window.evidence_start_ms}-{window.evidence_end_ms} ms

Rules:
- Atomic states remain authoritative. Add an umbrella episode only when several consecutive states belong to one recognizable activity, attempt, encounter, setup, recovery, or interruption.
- Processing-window edges have no editorial meaning. An episode may extend anywhere inside the supplied overlapping evidence; the reconciliation pass will join matching candidates across windows.
- Level 1 is the meaningful parent activity, such as an entire character-creation session. Level 2 is an optional useful subdivision, such as equipment selection inside character creation. Use no deeper nesting.
- Menu changes do not automatically end an activity. Character statistics, equipment, skills, inventory, and final confirmation can all belong to one character-creation episode when they serve the same goal.
- Conversely, do not merge unrelated activities merely because they use similar screens or occur nearby.
- Use the same concise continuity_key for candidates that appear to continue beyond this window. parent_episode_key must name a level-1 episode_key from this response or be empty.
- Prefer a small number of strong episodes. Do not recreate every atomic node as an episode.
- Labels must be short factual activity names, not editorial judgments or strategic summaries. A summary may add one factual sentence, but must not combine unrelated events or explain future importance.

Atomic event states:
{json.dumps(local_nodes, ensure_ascii=False, separators=(',', ':'))}

Semantic evidence:
{json.dumps(local_spans, ensure_ascii=False, separators=(',', ':'))}

Completion: identify every meaningful umbrella activity supported by this evidence, preserve uncertain boundaries conservatively, and return only the JSON object required by the response schema.
"""
        parsed = _parse_response(
            provider.complete_structured(
                prompt,
                max_tokens=AUDIO_EDITORIAL_OUTPUT_MAX_TOKENS,
                operation="editorial_episode_map",
                response_schema=ACTIVITY_EPISODE_RESPONSE_SCHEMA,
            )
        )
        normalized = _normalize_episode_candidates(
            parsed.get("episodes"),
            source_id=source_id,
            source_duration_ms=source_duration_ms,
            event_nodes=nodes,
            range_start_ms=window.evidence_start_ms,
            range_end_ms=window.evidence_end_ms,
            prefix=f"window-{window.index:04d}",
        )
        if candidate_completed is not None:
            candidate_completed(window.index, normalized)
        return normalized

    candidates: list[dict[str, Any]] = []
    workers = max(1, min(int(max_workers), len(analysis_windows)))
    if progress is not None:
        progress(
            locale_label(
                output_locale,
                f"grouping atomic states into activity episodes across {len(analysis_windows)} range(s)...",
                f"原子的な状態を {len(analysis_windows)} 区間の活動エピソードへ統合中…",
            )
        )
    if workers == 1:
        for window in analysis_windows:
            candidates.extend(map_window(window))
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="editorial-episodes") as pool:
            futures = [pool.submit(map_window, window) for window in analysis_windows]
            for future in as_completed(futures):
                candidates.extend(future.result())
    if not candidates:
        return []
    candidates.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["level"]))
    reconcile_prompt = f"""Task: reconcile local activity-episode candidates into one canonical bounded episode layer.

Output language: {output_language_instruction(output_locale)}
Title/game: {title_or_game}
Recording objective: {objective}
Source ID: {source_id}
Source duration: {source_duration_ms} ms

Rules:
- Processing ranges overlapped and their edges have no editorial meaning. Join candidates with the same real activity even when their labels or exact edges differ.
- Preserve the atomic event timeline; this layer supplies parent activities, not replacement event boundaries.
- Level 1 contains complete recognizable activities. Level 2 contains only useful subdivisions and must name a containing level-1 parent_episode_key from this response. Use no deeper nesting.
- A complete character-creation, loadout, retry, boss attempt, conversation, or recovery episode may span many menu and visual substates.
- Level-1 episodes are the canonical primary activity timeline and must not overlap one another. Put a simultaneous narrower activity in level 2 under its containing level-1 episode instead of creating another overlapping level-1 episode. Avoid duplicate episodes and crossing parent/child relationships.
- Use exact source times supported by the candidates. Prefer conservative outer boundaries when matching candidates clearly continue across a processing edge.
- Return at most 120 episodes.

Local candidates:
{json.dumps(candidates, ensure_ascii=False, separators=(',', ':'))}

Completion: reconcile all local candidates, join cross-window continuations, retain useful nesting, and return only the JSON object required by the response schema.
"""
    parsed = _parse_response(
        provider.complete_structured(
            reconcile_prompt,
            max_tokens=EDITORIAL_OUTPUT_MAX_TOKENS,
            operation="editorial_episode_reconcile",
            response_schema=ACTIVITY_EPISODE_RESPONSE_SCHEMA,
        )
    )
    episodes = _normalize_episode_candidates(
        parsed.get("episodes"),
        source_id=source_id,
        source_duration_ms=source_duration_ms,
        event_nodes=nodes,
        range_start_ms=0,
        range_end_ms=source_duration_ms,
        prefix="canonical",
    )[:120]
    _resolve_episode_parents(episodes)
    for index, episode in enumerate(episodes, 1):
        episode["episode_id"] = f"{source_id}:episode:{index:04d}"
    key_to_id = {
        str(item.get("episode_key")): str(item["episode_id"])
        for item in episodes
    }
    for episode in episodes:
        parent_key = str(episode.pop("parent_episode_key", "") or "")
        episode["parent_episode_id"] = key_to_id.get(parent_key, "")
        episode.pop("episode_key", None)
    for node in nodes:
        node["activity_episode_ids"] = [
            str(item["episode_id"])
            for item in episodes
            if _items_overlap(node, item)
        ]
    if canonical_completed is not None:
        canonical_completed(episodes)
    return episodes


def _compact_episode_event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": item.get("event_id"),
        "start_ms": item.get("start_ms"),
        "end_ms": item.get("end_ms"),
        "visual_category": item.get("visual_category"),
        "observed_label": str(
            item.get("observed_label") or item.get("visual_state") or ""
        )[:80],
        "possible_interruption": bool(item.get("possible_interruption")),
    }


def _normalize_episode_candidates(
    value: Any,
    *,
    source_id: str,
    source_duration_ms: int,
    event_nodes: Sequence[dict[str, Any]],
    range_start_ms: int,
    range_end_ms: int,
    prefix: str,
) -> list[dict[str, Any]]:
    result = []
    seen: set[tuple[int, int, int, str]] = set()
    for index, item in enumerate(_object_list(value)):
        start_ms = max(range_start_ms, _integer(item.get("start_ms"), range_start_ms))
        end_ms = min(range_end_ms, _integer(item.get("end_ms"), range_end_ms))
        overlapping = [node for node in event_nodes if _items_overlap(node, {"start_ms": start_ms, "end_ms": end_ms})]
        if overlapping:
            start_ms = max(range_start_ms, min(_integer(node.get("start_ms"), start_ms) for node in overlapping))
            end_ms = min(range_end_ms, max(_integer(node.get("end_ms"), end_ms) for node in overlapping))
        if end_ms <= start_ms:
            continue
        level = 2 if _integer(item.get("level"), 1) == 2 else 1
        continuity_key = _text(item.get("continuity_key"))[:160]
        identity = (start_ms, end_ms, level, continuity_key.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        kind = str(item.get("episode_kind") or "other")
        if kind not in ACTIVITY_EPISODE_KINDS:
            kind = "other"
        result.append(
            {
                "episode_key": _text(item.get("episode_key")) or f"{prefix}-{index + 1:03d}",
                "level": level,
                "parent_episode_key": _text(item.get("parent_episode_key")),
                "episode_kind": kind,
                "source_id": source_id,
                "start_ms": start_ms,
                "end_ms": min(source_duration_ms, end_ms),
                "label": _short_factual_label(item.get("label")),
                "summary": _short_factual_summary(item.get("summary")),
                "continuity_key": continuity_key,
                "confidence": _confidence(item.get("confidence")),
            }
        )
    result.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["level"]))
    return result


def _resolve_episode_parents(episodes: Sequence[dict[str, Any]]) -> None:
    parents = {
        str(item.get("episode_key")): item
        for item in episodes
        if item.get("level") == 1 and item.get("episode_key")
    }
    for item in episodes:
        if item.get("level") != 2:
            item["parent_episode_key"] = ""
            continue
        parent = parents.get(str(item.get("parent_episode_key") or ""))
        if (
            parent is None
            or int(parent["start_ms"]) > int(item["start_ms"])
            or int(parent["end_ms"]) < int(item["end_ms"])
        ):
            containing = [
                candidate
                for candidate in parents.values()
                if int(candidate["start_ms"]) <= int(item["start_ms"])
                and int(candidate["end_ms"]) >= int(item["end_ms"])
            ]
            parent = min(
                containing,
                key=lambda candidate: int(candidate["end_ms"]) - int(candidate["start_ms"]),
            ) if containing else None
        item["parent_episode_key"] = str(parent.get("episode_key")) if parent else ""


def _short_factual_label(value: Any) -> str:
    return " ".join(_text(value).split())[:80]


def _short_factual_summary(value: Any) -> str:
    text = " ".join(_text(value).split())
    for separator in ("。", ". ", "！", "! ", "？", "? "):
        if separator in text:
            text = text.split(separator, 1)[0] + separator.strip()
            break
    return text[:240]


def _best_overlap_visual(
    values: Sequence[VisualEvidence], start_ms: int, end_ms: int
) -> VisualEvidence | None:
    candidates = [item for item in values if item.end_ms > start_ms and item.start_ms < end_ms]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            min(end_ms, item.end_ms) - max(start_ms, item.start_ms),
            item.confidence,
        ),
    )


def _visual_states_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("state_signature") == right.get("state_signature"):
        return True
    left_tags = {str(value).casefold() for value in left.get("visual_tags", [])}
    right_tags = {str(value).casefold() for value in right.get("visual_tags", [])}
    overlap = len(left_tags & right_tags)
    union = len(left_tags | right_tags)
    return (
        left.get("visual_category") == right.get("visual_category")
        and left.get("observed_label") == right.get("observed_label")
        and union > 0
        and overlap / union >= 0.5
    )


def _event_nodes_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _visual_states_match(left, right)


def _observed_state_label(visual: VisualEvidence | None) -> str:
    if visual is None:
        return ""
    supplied = " ".join(visual.observed_label.split())
    if supplied:
        return supplied[:80]
    description = " ".join(visual.description.split())
    for separator in ("。", ". ", "！", "! ", "？", "? "):
        if separator in description:
            description = description.split(separator, 1)[0]
            break
    return (description or visual.visual_category.replace("_", " "))[:80]






def analyze_editorial_source(
    *,
    provider: EditorialPlanningProvider,
    source_id: str,
    source_duration_ms: int,
    title_or_game: str,
    objective: str,
    transcript: Sequence[TranscriptEvidence],
    visuals: Sequence[VisualEvidence],
    cumulative_context: dict[str, Any] | None = None,
    acoustic_events: Sequence[dict[str, Any]] = (),
    temporal_bursts: Sequence[dict[str, Any]] = (),
    game_knowledge: str = "",
    window_ms: int = DEFAULT_WINDOW_MS,
    progress: Callable[[str], None] | None = None,
    completed_windows: Sequence[dict[str, Any]] = (),
    window_completed: Callable[[dict[str, Any]], None] | None = None,
    output_locale: str = "en",
    max_workers: int = MAX_EDITORIAL_WINDOW_WORKERS,
) -> dict[str, Any]:
    """Build a source event map from safe overlapping processing windows.

    Processing windows are an implementation detail. Their evidence overlaps,
    their core boundaries avoid spoken phrases, and their findings are merged
    back into one source timeline before any project-wide planning occurs.
    """
    if not source_id.strip() or not title_or_game.strip() or not objective.strip():
        raise SubtitlerError("Editorial source ID, title/game, and objective are required")
    if source_duration_ms <= 0 or window_ms <= 0:
        raise SubtitlerError("Editorial source and analysis-window durations must be positive")
    initial_context = _normalized_context(cumulative_context or {})
    context = _normalized_context(initial_context)
    windows: list[dict[str, Any]] = []
    all_spans: list[dict[str, Any]] = []
    cached_windows = {
        int(item.get("base_window_index", -1)): item
        for item in completed_windows
        if isinstance(item, dict)
    }
    completed_records: dict[int, dict[str, Any]] = {}
    analysis_windows = build_editorial_analysis_windows(
        source_duration_ms,
        transcript=transcript,
        visuals=visuals,
        temporal_bursts=temporal_bursts,
        target_window_ms=window_ms,
    )
    window_count = len(analysis_windows)

    def analyze_window(window: EditorialAnalysisWindow) -> tuple[EditorialAnalysisWindow, list[dict[str, Any]], dict[str, list[Any]]]:
        cached = cached_windows.get(window.index)
        if _valid_cached_window(
            cached,
            window.start_ms,
            window.end_ms,
            window.evidence_start_ms,
            window.evidence_end_ms,
        ):
            if progress is not None:
                progress(
                    locale_label(
                        output_locale,
                        f"reusing completed event window {window.index + 1}/{window_count} "
                        f"({_clock(window.start_ms)}-{_clock(window.end_ms)}).",
                        f"完了済みイベントウィンドウ {window.index + 1}/{window_count} を再利用 "
                        f"({_clock(window.start_ms)}-{_clock(window.end_ms)})。",
                    )
                )
            return (
                window,
                [dict(item) for item in cached["windows"] if isinstance(item, dict)],
                _normalized_context(cached["cumulative_context_after"]),
            )
        if progress is not None:
            progress(
                locale_label(
                    output_locale,
                    f"mapping event window {window.index + 1}/{window_count} "
                    f"({_clock(window.start_ms)}-{_clock(window.end_ms)})...",
                    f"イベントウィンドウ {window.index + 1}/{window_count} をマッピング中 "
                    f"({_clock(window.start_ms)}-{_clock(window.end_ms)})…",
                )
            )
        normalized_windows, window_context = _analyze_range_with_overflow_recovery(
            provider=provider,
            source_id=source_id,
            source_duration_ms=source_duration_ms,
            title_or_game=title_or_game,
            objective=objective,
            transcript=transcript,
            visuals=visuals,
            cumulative_context=initial_context,
            acoustic_events=acoustic_events,
            temporal_bursts=temporal_bursts,
            game_knowledge=game_knowledge,
            output_locale=output_locale,
            window_start_ms=window.start_ms,
            window_end_ms=window.end_ms,
            evidence_start_ms=window.evidence_start_ms,
            evidence_end_ms=window.evidence_end_ms,
            analysis_index=window.index * 100,
            progress=progress,
        )
        completed = {
            "prompt_version": EDITORIAL_PROMPT_VERSION,
            "base_window_index": window.index,
            "start_ms": window.start_ms,
            "end_ms": window.end_ms,
            "evidence_start_ms": window.evidence_start_ms,
            "evidence_end_ms": window.evidence_end_ms,
            "windows": normalized_windows,
            "cumulative_context_after": window_context,
        }
        completed_records[window.index] = completed
        if window_completed is not None:
            window_completed(completed)
        return window, normalized_windows, window_context

    completed_results: dict[int, tuple[list[dict[str, Any]], dict[str, list[Any]]]] = {}
    workers = max(1, min(int(max_workers), window_count))
    if workers == 1:
        for analysis_window in analysis_windows:
            window, normalized_windows, window_context = analyze_window(analysis_window)
            completed_results[window.index] = (normalized_windows, window_context)
    else:
        if progress is not None:
            progress(
                locale_label(
                    output_locale,
                    f"running up to {workers} independent event windows in parallel.",
                    f"独立したイベントウィンドウを最大 {workers} 件並列で解析します。",
                )
            )
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="editorial-event-map") as pool:
            futures = [pool.submit(analyze_window, window) for window in analysis_windows]
            for future in as_completed(futures):
                window, normalized_windows, window_context = future.result()
                completed_results[window.index] = (normalized_windows, window_context)

    for analysis_window in analysis_windows:
        normalized_windows, window_context = completed_results[analysis_window.index]
        context = merge_cumulative_context(context, window_context)
        for normalized in normalized_windows:
            windows.append(normalized)
            all_spans.extend(normalized["semantic_spans"])
        if progress is not None:
            progress(
                locale_label(
                    output_locale,
                    f"event window {analysis_window.index + 1}/{window_count} complete: "
                    f"{sum(len(item['semantic_spans']) for item in normalized_windows)} factual span(s).",
                    f"イベントウィンドウ {analysis_window.index + 1}/{window_count} が完了: "
                    f"事実区間 {sum(len(item['semantic_spans']) for item in normalized_windows)} 件。",
                )
            )
    all_spans = stitch_editorial_event_spans(all_spans)
    utterance_groups = build_utterance_groups(transcript)
    event_graph = build_editorial_event_graph(
        source_id=source_id,
        source_duration_ms=source_duration_ms,
        visuals=visuals,
        semantic_spans=all_spans,
        audio_intents=(),
    )
    cached_episode_candidates = {
        index: [dict(item) for item in value.get("activity_episode_candidates", [])]
        for index, value in cached_windows.items()
        if isinstance(value.get("activity_episode_candidates"), list)
    }
    canonical_record = next(
        (
            value
            for value in cached_windows.values()
            if isinstance(value.get("canonical_activity_episodes"), list)
        ),
        None,
    )

    def persist_episode_progress(index: int, field: str, value: Any) -> None:
        record = dict(completed_records.get(index) or cached_windows.get(index) or {})
        if not record:
            return
        record[field] = value
        completed_records[index] = record
        if window_completed is not None:
            window_completed(record)

    activity_episodes = build_activity_episode_layer(
        provider=provider,
        source_id=source_id,
        source_duration_ms=source_duration_ms,
        title_or_game=title_or_game,
        objective=objective,
        event_graph=event_graph,
        semantic_spans=all_spans,
        analysis_windows=analysis_windows,
        output_locale=output_locale,
        max_workers=max_workers,
        progress=progress,
        cached_candidates=cached_episode_candidates,
        candidate_completed=lambda index, values: persist_episode_progress(
            index, "activity_episode_candidates", values
        ),
        cached_canonical=(
            canonical_record.get("canonical_activity_episodes")
            if canonical_record is not None
            else None
        ),
        canonical_completed=lambda values: persist_episode_progress(
            analysis_windows[0].index,
            "canonical_activity_episodes",
            values,
        ),
    )
    return {
        "prompt_version": EDITORIAL_PROMPT_VERSION,
        "source_id": source_id,
        "source_duration_ms": source_duration_ms,
        "windows": windows,
        "semantic_spans": all_spans,
        # Empty legacy collections keep old checkpoint readers tolerant without
        # asking the active factual pass to manufacture discarded edit advice.
        "recommendations": [],
        "narration_briefs": [],
        "connections": [],
        "creative_suggestions": [],
        "audio_intent_spans": [],
        "event_graph": event_graph,
        "activity_episodes": activity_episodes,
        "timeline_coverage": [],
        "safe_boundaries_ms": editorial_safe_boundaries(
            source_duration_ms,
            transcript=[
                TranscriptEvidence(int(item["start_ms"]), int(item["end_ms"]), str(item["text"]))
                for item in utterance_groups
            ],
            semantic_spans=all_spans,
            event_graph=event_graph,
        ),
        "speech_segments": [
            dict(span)
            for utterance in utterance_groups
            for span in utterance.get("speech_spans", [])
            if isinstance(span, dict)
        ],
        "utterance_groups": utterance_groups,
        "cumulative_context": context,
    }


def build_editorial_analysis_windows(
    source_duration_ms: int,
    *,
    transcript: Sequence[TranscriptEvidence],
    visuals: Sequence[VisualEvidence] = (),
    temporal_bursts: Sequence[dict[str, Any]] = (),
    target_window_ms: int = DEFAULT_WINDOW_MS,
    overlap_ms: int = DEFAULT_WINDOW_OVERLAP_MS,
) -> list[EditorialAnalysisWindow]:
    """Create processing windows whose core edges avoid active speech."""
    if source_duration_ms <= 0 or target_window_ms <= 0:
        raise ValueError("Editorial analysis durations must be positive")
    edges = [0]
    while source_duration_ms - edges[-1] > target_window_ms:
        ideal = edges[-1] + target_window_ms
        boundary = _choose_safe_processing_boundary(
            ideal,
            lower=max(edges[-1] + target_window_ms // 2, ideal - SAFE_BOUNDARY_SEARCH_MS),
            upper=min(source_duration_ms - 1, ideal + SAFE_BOUNDARY_SEARCH_MS),
            transcript=transcript,
            visuals=visuals,
            temporal_bursts=temporal_bursts,
        )
        if boundary <= edges[-1]:
            boundary = min(source_duration_ms, edges[-1] + target_window_ms)
        edges.append(boundary)
    if edges[-1] != source_duration_ms:
        edges.append(source_duration_ms)
    return [
        EditorialAnalysisWindow(
            index=index,
            start_ms=start_ms,
            end_ms=end_ms,
            evidence_start_ms=max(0, start_ms - overlap_ms),
            evidence_end_ms=min(source_duration_ms, end_ms + overlap_ms),
        )
        for index, (start_ms, end_ms) in enumerate(zip(edges, edges[1:]))
        if end_ms > start_ms
    ]


def _choose_safe_processing_boundary(
    ideal_ms: int,
    *,
    lower: int,
    upper: int,
    transcript: Sequence[TranscriptEvidence],
    visuals: Sequence[VisualEvidence],
    temporal_bursts: Sequence[dict[str, Any]],
) -> int:
    candidates: dict[int, float] = {}
    ordered = sorted(transcript, key=lambda item: (item.start_ms, item.end_ms))
    for index, item in enumerate(ordered):
        if lower <= item.start_ms <= upper:
            candidates[item.start_ms] = max(candidates.get(item.start_ms, 0.0), 0.35)
        if lower <= item.end_ms <= upper:
            next_start = ordered[index + 1].start_ms if index + 1 < len(ordered) else item.end_ms
            gap_bonus = min(0.5, max(0, next_start - item.end_ms) / 10_000.0)
            candidates[item.end_ms] = max(candidates.get(item.end_ms, 0.0), 0.45 + gap_bonus)
    for item in visuals:
        for point in (item.start_ms, item.end_ms):
            if lower <= point <= upper:
                candidates[point] = max(candidates.get(point, 0.0), 0.2)
    for item in temporal_bursts:
        point = _integer(item.get("timestamp_ms"), -1)
        if lower <= point <= upper:
            candidates[point] = max(candidates.get(point, 0.0), 0.15)
    if not candidates:
        return max(lower, min(upper, ideal_ms))
    scale = max(1.0, float(upper - lower))
    return min(
        candidates,
        key=lambda point: abs(point - ideal_ms) / scale - candidates[point],
    )


def editorial_safe_boundaries(
    source_duration_ms: int,
    *,
    transcript: Sequence[TranscriptEvidence],
    semantic_spans: Sequence[dict[str, Any]] = (),
    event_graph: dict[str, Any] | None = None,
) -> list[int]:
    values = {0, source_duration_ms}
    for item in transcript:
        values.add(max(0, min(source_duration_ms, item.start_ms)))
        values.add(max(0, min(source_duration_ms, item.end_ms)))
    for item in semantic_spans:
        values.add(max(0, min(source_duration_ms, _integer(item.get("start_ms"), 0))))
        values.add(max(0, min(source_duration_ms, _integer(item.get("end_ms"), 0))))
    for item in _object_list((event_graph or {}).get("nodes")):
        values.add(max(0, min(source_duration_ms, _integer(item.get("start_ms"), 0))))
        values.add(max(0, min(source_duration_ms, _integer(item.get("end_ms"), 0))))
    return sorted(values)


def stitch_editorial_event_spans(values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join adjacent window fragments when they describe the same continuing event."""
    ordered = sorted(
        (dict(item) for item in values if int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))),
        key=lambda item: (int(item["start_ms"]), int(item["end_ms"])),
    )
    result: list[dict[str, Any]] = []
    for item in ordered:
        if not result:
            result.append(item)
            continue
        previous = result[-1]
        gap = int(item["start_ms"]) - int(previous["end_ms"])
        same_kind = _text(item.get("kind")).casefold() == _text(previous.get("kind")).casefold()
        label_terms = _event_terms(item.get("label"))
        previous_terms = _event_terms(previous.get("label"))
        similar_label = bool(label_terms and previous_terms and label_terms & previous_terms)
        if gap <= 2_000 and same_kind and similar_label:
            previous["end_ms"] = max(int(previous["end_ms"]), int(item["end_ms"]))
            summaries = [_text(previous.get("summary")), _text(item.get("summary"))]
            previous["summary"] = " ".join(dict.fromkeys(value for value in summaries if value))[:1600]
            previous["evidence_refs"] = list(
                dict.fromkeys([*_string_list(previous.get("evidence_refs")), *_string_list(item.get("evidence_refs"))])
            )[:20]
            previous["confidence"] = min(
                _confidence(previous.get("confidence")), _confidence(item.get("confidence"))
            )
            continue
        result.append(item)
    return result




def _event_terms(value: Any) -> set[str]:
    return {
        token
        for token in _text(value).casefold().replace("_", " ").replace("-", " ").split()
        if len(token) >= 2
    }


def _analyze_range_with_overflow_recovery(
    *,
    provider: EditorialPlanningProvider,
    source_id: str,
    source_duration_ms: int,
    title_or_game: str,
    objective: str,
    transcript: Sequence[TranscriptEvidence],
    visuals: Sequence[VisualEvidence],
    cumulative_context: dict[str, Any],
    acoustic_events: Sequence[dict[str, Any]],
    temporal_bursts: Sequence[dict[str, Any]],
    game_knowledge: str,
    output_locale: str,
    window_start_ms: int,
    window_end_ms: int,
    evidence_start_ms: int,
    evidence_end_ms: int,
    analysis_index: int,
    progress: Callable[[str], None] | None,
    split_depth: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    evidence_start = evidence_start_ms
    evidence_end = evidence_end_ms
    transcript_window = [
        item
        for item in transcript
        if item.end_ms > evidence_start and item.start_ms < evidence_end
    ]
    visual_window = [
        item
        for item in visuals
        if item.end_ms > evidence_start and item.start_ms < evidence_end
    ]
    local_acoustic = [
        item
        for item in acoustic_events
        if int(item.get("end_ms", 0)) > evidence_start
        and int(item.get("start_ms", 0)) < evidence_end
    ]
    local_bursts = [
        item
        for item in temporal_bursts
        if evidence_start <= int(item.get("timestamp_ms", 0)) < evidence_end
    ]
    prompt = build_editorial_prompt(
        source_id=source_id,
        source_duration_ms=source_duration_ms,
        window_index=analysis_index,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        evidence_start_ms=evidence_start,
        evidence_end_ms=evidence_end,
        title_or_game=title_or_game,
        objective=objective,
        transcript=transcript_window,
        visuals=visual_window,
        cumulative_context=cumulative_context,
        acoustic_events=local_acoustic,
        temporal_bursts=local_bursts,
        game_knowledge=game_knowledge,
        output_locale=output_locale,
    )
    try:
        raw = provider.complete_structured(
            prompt,
            max_tokens=EDITORIAL_OUTPUT_MAX_TOKENS,
            operation="editorial_map",
            response_schema=EDITORIAL_MAP_RESPONSE_SCHEMA,
        )
    except StructuredOutputIncompleteError as exc:
        duration_ms = window_end_ms - window_start_ms
        if (
            exc.reason != "max_output_tokens"
            or split_depth >= MAX_OVERFLOW_SPLIT_DEPTH
            or duration_ms < MIN_OVERFLOW_SPLIT_MS * 2
        ):
            raise
        split_ms = _choose_overflow_split_ms(
            window_start_ms,
            window_end_ms,
            transcript_window,
            visual_window,
            local_bursts,
        )
        if progress is not None:
            progress(
                locale_label(
                    output_locale,
                    "hosted output limit reached; retrying only this range as two contextual "
                    f"subwindows at {_clock(split_ms)}.",
                    "ホストモデルの出力上限に達したため、この区間だけを "
                    f"{_clock(split_ms)} で二つに分け、文脈を維持して再試行します。",
                )
            )
        left_windows, left_context = _analyze_range_with_overflow_recovery(
            provider=provider,
            source_id=source_id,
            source_duration_ms=source_duration_ms,
            title_or_game=title_or_game,
            objective=objective,
            transcript=transcript,
            visuals=visuals,
            cumulative_context=cumulative_context,
            acoustic_events=acoustic_events,
            temporal_bursts=temporal_bursts,
            game_knowledge=game_knowledge,
            output_locale=output_locale,
            window_start_ms=window_start_ms,
            window_end_ms=split_ms,
            evidence_start_ms=evidence_start,
            evidence_end_ms=min(evidence_end, split_ms + DEFAULT_WINDOW_OVERLAP_MS),
            analysis_index=analysis_index,
            progress=progress,
            split_depth=split_depth + 1,
        )
        right_windows, right_context = _analyze_range_with_overflow_recovery(
            provider=provider,
            source_id=source_id,
            source_duration_ms=source_duration_ms,
            title_or_game=title_or_game,
            objective=objective,
            transcript=transcript,
            visuals=visuals,
            cumulative_context=left_context,
            acoustic_events=acoustic_events,
            temporal_bursts=temporal_bursts,
            game_knowledge=game_knowledge,
            output_locale=output_locale,
            window_start_ms=split_ms,
            window_end_ms=window_end_ms,
            evidence_start_ms=max(evidence_start, split_ms - DEFAULT_WINDOW_OVERLAP_MS),
            evidence_end_ms=evidence_end,
            analysis_index=analysis_index + len(left_windows),
            progress=progress,
            split_depth=split_depth + 1,
        )
        return left_windows + right_windows, right_context
    parsed = _parse_response(raw)
    normalized = _normalize_window_result(
        parsed,
        source_id=source_id,
        window_index=analysis_index,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )
    context = merge_cumulative_context(
        cumulative_context, normalized.pop("context_update")
    )
    return [normalized], context


def _choose_overflow_split_ms(
    start_ms: int,
    end_ms: int,
    transcript: Sequence[TranscriptEvidence],
    visuals: Sequence[VisualEvidence],
    temporal_bursts: Sequence[dict[str, Any]],
) -> int:
    """Prefer a quiet or visually recognized boundary near the range midpoint."""
    midpoint = (start_ms + end_ms) // 2
    lower = start_ms + MIN_OVERFLOW_SPLIT_MS
    upper = end_ms - MIN_OVERFLOW_SPLIT_MS
    candidates: dict[int, float] = {}
    ordered_transcript = sorted(transcript, key=lambda item: (item.start_ms, item.end_ms))
    for previous, current in zip(ordered_transcript, ordered_transcript[1:]):
        gap_start = max(start_ms, previous.end_ms)
        gap_end = min(end_ms, current.start_ms)
        if gap_end - gap_start >= 1_000:
            point = (gap_start + gap_end) // 2
            candidates[point] = max(
                candidates.get(point, 0.0), min(0.45, (gap_end - gap_start) / 20_000.0)
            )
    for item in visuals:
        for point in (item.start_ms, item.end_ms):
            candidates[point] = max(candidates.get(point, 0.0), 0.15)
    for item in temporal_bursts:
        point = int(item.get("timestamp_ms", 0))
        candidates[point] = max(candidates.get(point, 0.0), 0.25)
    eligible = [(point, bonus) for point, bonus in candidates.items() if lower <= point <= upper]
    if not eligible:
        return max(lower, min(upper, midpoint))
    half_duration = max(1.0, (end_ms - start_ms) / 2.0)
    return min(
        eligible,
        key=lambda candidate: abs(candidate[0] - midpoint) / half_duration - candidate[1],
    )[0]


def _valid_cached_window(
    value: Any,
    start_ms: int,
    end_ms: int,
    evidence_start_ms: int | None = None,
    evidence_end_ms: int | None = None,
) -> bool:
    return (
        isinstance(value, dict)
        and value.get("prompt_version") == EDITORIAL_PROMPT_VERSION
        and int(value.get("start_ms", -1)) == start_ms
        and int(value.get("end_ms", -1)) == end_ms
        and isinstance(value.get("windows"), list)
        and bool(value["windows"])
        and all(
            isinstance(item, dict) and isinstance(item.get("semantic_spans"), list)
            for item in value["windows"]
        )
        and isinstance(value.get("cumulative_context_after"), dict)
        and (
            evidence_start_ms is None
            or int(value.get("evidence_start_ms", -1)) == evidence_start_ms
        )
        and (
            evidence_end_ms is None
            or int(value.get("evidence_end_ms", -1)) == evidence_end_ms
        )
    )


def synthesize_human_information_project(
    *,
    provider: EditorialPlanningProvider,
    project: dict[str, Any],
) -> dict[str, Any]:
    """Build a factual multi-scale story map and selective narration briefs."""
    source_summaries: list[dict[str, Any]] = []
    source_durations: dict[str, int] = {}
    for source in sorted(project.get("sources", []), key=lambda item: item.get("order", 0)):
        source_id = str(source.get("source_id") or "")
        duration_ms = int(source.get("duration_ms", 0))
        source_durations[source_id] = duration_ms
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        semantic = source.get("stages", {}).get("semantic_spans", {}).get("output")
        windows = semantic.get("windows", []) if isinstance(semantic, dict) else []
        source_summaries.append(
            {
                "source_id": source_id,
                "order": source.get("order"),
                "duration_ms": duration_ms,
                "window_summaries": [
                    str(item.get("summary") or "")[:1000]
                    for item in windows
                    if isinstance(item, dict) and str(item.get("summary") or "").strip()
                ],
                "semantic_spans": [
                    {
                        "start_ms": item.get("start_ms"),
                        "end_ms": item.get("end_ms"),
                        "label": item.get("label"),
                        "kind": item.get("kind"),
                        "summary": str(item.get("summary") or "")[:700],
                    }
                    for item in result.get("semantic_spans", [])
                    if isinstance(item, dict)
                ][:600],
                "activity_episodes": [
                    dict(item)
                    for item in result.get("activity_episodes", [])
                    if isinstance(item, dict)
                ][:180],
                "event_graph": _compact_event_graph(result.get("event_graph")),
                "full_aligned_transcript": [
                    asdict(item) for item in _load_project_source_transcript(source)
                ],
            }
        )
    prompt = f"""Task: synthesize a factual, human-facing information map for a long gameplay recording project.

Output language: {output_language_instruction(project.get('output_locale', 'en'))}
Title/game: {project.get('title_or_game', '')}
Recording objective: {project.get('objective', '')}

The application is an evidence dashboard for a human editor. Build a rich multi-scale account of what
happened and propose post-recorded narration only where it closes a real viewer knowledge gap. The human
will decide every cut. Produce no cut, preserve, trim, montage, highlight, duration, or creative-effect
recommendations.

Narration practice:
{EDITORIAL_PRACTICES_POLICY}

Rules:
- Treat the full transcript as the spoken backbone and the event graph as the scene-state backbone.
- event_phases are factual long-running activities: stages, areas, attempts, character creation, build
  development, boss battles, conversations, interruptions, and outcomes. They may overlap when they describe
  different scales or themes. Use evidence-supported boundaries rather than processing-window edges.
- story_threads capture long-horizon relationships. Each anchor must identify a concrete setup, development,
  reversal, callback, or payoff. Shared nouns alone do not establish a relationship.
- Narration is the sole editorial recommendation. Use it selectively for opaque mechanics, consequences,
  route/session bridges, retry compression, lore synthesis, callbacks, content mediation, or outcome context.
- Preserve discovery-state honesty. Later knowledge may explain an earlier mistake retrospectively but must
  not make the creator appear to know it during the original moment.
- Each narration brief is one cohesive passage. Merge adjacent ideas that serve one continuous explanation.
  State specific factual talking points and evidence-bearing representative visuals; avoid generic prompts.
- Prefer source audio for reactions, jokes, uncertainty, discovery, and payoff. End a narration passage where
  retained source audio can carry the moment itself.
- Opening context depends on title familiarity, series context, and the stated run objective. An obscure first
  look may need setup; a familiar game may not; a challenge premise may justify setup in either case.
- Do not infer victory, failure, completion, or causality from a visual label alone. Record unresolved evidence
  conflicts in uncertainties.
- Return at most 120 event phases, 24 story threads, and 16 narration briefs.

Ordered source evidence:
{json.dumps(source_summaries, ensure_ascii=False, separators=(',', ':'))}

Completion: the complete project has a factual progression summary, meaningful multi-scale phases, every
strong supported long-horizon thread, and only narration briefs that perform indispensable explanatory work.
Return only the required JSON object.
"""
    parsed = _parse_response(
        provider.complete_structured(
            prompt,
            max_tokens=DIRECTOR_OUTPUT_MAX_TOKENS,
            operation="editorial_human_information",
            response_schema=HUMAN_INFORMATION_SYNTHESIS_SCHEMA,
        )
    )

    phases: list[dict[str, Any]] = []
    for item in _object_list(parsed.get("event_phases")):
        source_id = str(item.get("source_id") or "")
        bounded = _valid_final_range(item, source_durations.get(source_id, 0))
        if bounded is None:
            continue
        phases.append(
            {
                "phase_id": f"phase-{len(phases) + 1:03d}",
                "source_id": source_id,
                "start_ms": bounded[0],
                "end_ms": bounded[1],
                "label": _text(item.get("label")),
                "summary": _text(item.get("summary")),
                "category": _text(item.get("category")) or "progression",
                "thread_ids": _string_list(item.get("thread_ids"))[:12],
            }
        )

    threads: list[dict[str, Any]] = []
    for item in _object_list(parsed.get("story_threads")):
        anchors: list[dict[str, Any]] = []
        for anchor in _object_list(item.get("anchors")):
            source_id = str(anchor.get("source_id") or "")
            bounded = _valid_final_range(anchor, source_durations.get(source_id, 0))
            if bounded is None:
                continue
            anchors.append(
                {
                    "source_id": source_id,
                    "start_ms": bounded[0],
                    "end_ms": bounded[1],
                    "label": _text(anchor.get("label")),
                    "relationship": _text(anchor.get("relationship")),
                }
            )
        if len(anchors) < 2:
            continue
        threads.append(
            {
                "thread_id": f"thread-{len(threads) + 1:03d}",
                "title": _text(item.get("title")),
                "summary": _text(item.get("summary")),
                "category": _text(item.get("category")) or "story",
                "anchors": anchors[:20],
            }
        )

    narration: list[dict[str, Any]] = []
    for item in _object_list(parsed.get("narration_briefs")):
        source_id = str(item.get("source_id") or "")
        bounded = _valid_final_range(item, source_durations.get(source_id, 0))
        if bounded is None:
            continue
        narration.append(
            {
                "id": f"narration-{len(narration) + 1:03d}",
                "source_id": source_id,
                "start_ms": bounded[0],
                "end_ms": bounded[1],
                "kind": str(item.get("kind") or "causal_bridge"),
                "purpose": _text(item.get("purpose")),
                "memory_jog": _text(item.get("memory_jog")),
                "talking_points": _string_list(item.get("talking_points"))[:12],
                "representative_visuals": _string_list(
                    item.get("representative_visuals")
                )[:12],
                "thread_ids": _string_list(item.get("thread_ids"))[:12],
            }
        )
    return {
        "workflow": "human_information",
        "progression_summary": _text(parsed.get("progression_summary")),
        "editorial_direction_summary": _text(parsed.get("progression_summary")),
        "event_phases": sorted(
            phases,
            key=lambda item: (
                next(
                    (
                        int(source.get("order", 0))
                        for source in project.get("sources", [])
                        if str(source.get("source_id")) == item["source_id"]
                    ),
                    0,
                ),
                item["start_ms"],
                item["end_ms"],
            ),
        ),
        "global_threads": threads,
        "payoff_threads": threads,
        "narration_briefs": narration,
        "connections": [],
        "conflicts": [],
        "uncertainties": _string_list(parsed.get("uncertainties"))[:20],
    }


















def select_editorial_subtitles(
    *,
    provider: EditorialPlanningProvider,
    project: dict[str, Any],
    final_actions: Sequence[dict[str, Any]],
    story_actions: Sequence[dict[str, Any]],
    progress: Callable[[str], None] | None = None,
    max_workers: int = MAX_EDITORIAL_WINDOW_WORKERS,
    default_keep: bool = False,
) -> list[dict[str, Any]]:
    """Select a contextual display-subtitle track after the story map is known."""
    units = _build_executable_planning_units(project)
    sources = {
        str(item.get("source_id")): item
        for item in project.get("sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    transcripts = {
        source_id: _load_project_source_transcript(source)
        for source_id, source in sources.items()
    }

    def run_unit(unit: dict[str, Any]) -> list[dict[str, Any]]:
        source = sources[unit["source_id"]]
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        transcript = [
            asdict(item)
            for item in transcripts.get(unit["source_id"], [])
            if item.end_ms > unit["evidence_start_ms"]
            and item.start_ms < unit["evidence_end_ms"]
        ]
        actions = [
            item for item in final_actions
            if isinstance(item, dict)
            and str(item.get("source_id")) == unit["source_id"]
            and _items_overlap(item, unit)
        ]
        beats = [
            item for item in story_actions
            if isinstance(item, dict)
            and str(item.get("source_id")) == unit["source_id"]
            and _items_overlap(item, unit)
        ]
        semantic_spans = [
            item for item in result.get("semantic_spans", [])
            if isinstance(item, dict) and _items_overlap(item, unit)
        ]
        event_graph = result.get("event_graph") if isinstance(result.get("event_graph"), dict) else {}
        event_nodes = [
            item for item in _object_list(event_graph.get("nodes"))
            if _items_overlap(item, unit)
        ]
        prompt = f"""Task: select the spoken thoughts that benefit from appearing as on-screen subtitles in one mapped section of a long-form video.

Output language: {output_language_instruction(project.get('output_locale', 'en'))}
Title/game: {project.get('title_or_game', '')}
Recording objective: {project.get('objective', '')}
Source ID: {unit['source_id']}
Core selection range: {unit['start_ms']}-{unit['end_ms']} ms
Overlapping context only: {unit['evidence_start_ms']}-{unit['evidence_end_ms']} ms

Rules:
- The factual story map and narration briefs are authoritative context. Select phrases only where source speech remains audible. Never select speech from a proposed narrated replacement or narration bridge.
- Use a generous selective track: roughly two to three times the density of an occasional emphasis-only track while leaving routine speech undisplayed.
- A thought earns display when its wording, delivery, or informational role helps the viewer: a reaction or punchline, a concise rule or objective, a consequential realization, a reveal, a callback/payoff, an emotionally important exchange, or wording the viewer should retain.
- Judge meaning in surrounding speech and story context. Do not select isolated fragments that are confusing without the preceding or following phrase.
- Prefer a complete single thought or coherent multi-phrase sequence. Longer strings are valid when they express one meaning unit.
- exact_phrase must be copied verbatim as one contiguous substring from the supplied transcript. Do not clean, rewrite, translate, or invent it.
- Use the approximate spoken range from the transcript. Deterministic token alignment will resolve the exact frame timing afterward.
- emphasis_energy ranges from -1.0 for subdued/calm/sad delivery, through 0.0 for neutral, to 1.0 for excited/forceful delivery.
- Return no more than 18 selected phrases in this core range, and return an empty list when none earn display.

Broad story beats:
{json.dumps(beats, ensure_ascii=False, separators=(',', ':'))}

Final source-timed edit plan:
{json.dumps(actions, ensure_ascii=False, separators=(',', ':'))}

Story/event timeline:
{json.dumps(semantic_spans, ensure_ascii=False, separators=(',', ':'))}

Atomic event nodes:
{json.dumps(event_nodes, ensure_ascii=False, separators=(',', ':'))}

Raw aligned transcript:
{json.dumps(transcript, ensure_ascii=False, separators=(',', ':'))}

Completion: every selected phrase is verbatim, contextually intelligible, inside the core range, and survives the supplied edit plan. Return only the required JSON object.
"""
        parsed = _parse_response(
            provider.complete_structured(
                prompt,
                max_tokens=8_192,
                operation="editorial_selective_subtitles",
                response_schema=SELECTIVE_SUBTITLE_RESPONSE_SCHEMA,
            )
        )
        selected: list[dict[str, Any]] = []
        for item in _object_list(parsed.get("selected_phrases")):
            start_ms, end_ms = _bounded_range(
                item, int(unit["start_ms"]), int(unit["end_ms"])
            )
            phrase = _text(item.get("exact_phrase"))
            if not phrase or end_ms <= start_ms:
                continue
            if not _subtitle_phrase_survives_plan(
                start_ms, end_ms, actions, default_keep=default_keep
            ):
                continue
            selected.append(
                {
                    "source_id": unit["source_id"],
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "source_text": phrase,
                    "reason": _text(item.get("reason")),
                    "emphasis_energy": max(
                        -1.0, min(1.0, float(item.get("emphasis_energy", 0.0)))
                    ),
                    "confidence": _confidence(item.get("confidence")),
                }
            )
        if progress is not None:
            progress(
                f"selective subtitle range {unit['ordinal']}/{len(units)} complete "
                f"({len(selected)} phrase(s))."
            )
        return selected

    if not units:
        return []
    workers = max(1, min(int(max_workers), len(units)))
    results: list[dict[str, Any]] = []
    if workers == 1:
        for unit in units:
            results.extend(run_unit(unit))
    else:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="editorial-subtitles"
        ) as pool:
            futures = [pool.submit(run_unit, unit) for unit in units]
            for future in as_completed(futures):
                results.extend(future.result())
    return sorted(
        results,
        key=lambda item: (
            next(
                int(source.get("order", 0))
                for source in project.get("sources", [])
                if str(source.get("source_id")) == str(item.get("source_id"))
            ),
            int(item.get("start_ms", 0)),
            int(item.get("end_ms", 0)),
        ),
    )


def _subtitle_phrase_survives_plan(
    start_ms: int,
    end_ms: int,
    actions: Sequence[dict[str, Any]],
    *,
    default_keep: bool = False,
) -> bool:
    midpoint = (start_ms + end_ms) // 2
    action = next(
        (
            item for item in actions
            if int(item.get("start_ms", 0)) <= midpoint < int(item.get("end_ms", 0))
        ),
        None,
    )
    if action is None:
        return default_keep
    if str(action.get("action_type")) in {
        "cut", "narrated_summary", "narration_bridge", "manual_review"
    }:
        return False
    operation_ranges = _object_list(action.get("operation_ranges"))
    if str(action.get("action_type")) == "trim":
        return not any(
            str(item.get("role")) == "remove" and _items_overlap(
                item, {"start_ms": start_ms, "end_ms": end_ms}
            )
            for item in operation_ranges
        )
    if str(action.get("action_type")) in {"extract_highlights", "montage"}:
        return any(
            str(item.get("role")) == "keep" and int(item.get("start_ms", 0)) <= midpoint < int(item.get("end_ms", 0))
            for item in operation_ranges
        )
    return True




def _build_executable_planning_units(project: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    target_ms = 12 * 60 * 1000
    for source in sorted(project.get("sources", []), key=lambda item: item.get("order", 0)):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        duration_ms = int(source.get("duration_ms", 0))
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        boundaries = sorted(
            set(
                int(value)
                for value in result.get("safe_boundaries_ms", [])
                if isinstance(value, int) and not isinstance(value, bool)
                and 0 <= value <= duration_ms
            )
        )
        semantic_spans = [item for item in result.get("semantic_spans", []) if isinstance(item, dict)]
        activity_episodes = [
            item
            for item in result.get("activity_episodes", [])
            if isinstance(item, dict) and int(item.get("level", 1)) == 1
        ]
        start_ms = 0
        while start_ms < duration_ms:
            ideal = min(duration_ms, start_ms + target_ms)
            if ideal == duration_ms:
                end_ms = duration_ms
            else:
                containing_episodes = [
                    item
                    for item in activity_episodes
                    if int(item.get("start_ms", 0)) < ideal < int(item.get("end_ms", 0))
                    and int(item.get("end_ms", 0)) <= ideal + MAX_EPISODE_UNIT_EXTENSION_MS
                ]
                if containing_episodes:
                    episode = min(
                        containing_episodes,
                        key=lambda item: (
                            int(item.get("end_ms", 0)) - ideal,
                            -float(item.get("confidence", 0.0)),
                        ),
                    )
                    ideal = int(episode.get("end_ms", ideal))
                candidates = [
                    value for value in boundaries
                    if start_ms + target_ms // 2 <= value <= min(duration_ms, ideal + SAFE_BOUNDARY_SEARCH_MS)
                    and not any(
                        int(span.get("start_ms", 0)) < value < int(span.get("end_ms", 0))
                        for span in semantic_spans
                    )
                ]
                if not candidates:
                    candidates = [
                        value for value in boundaries
                        if start_ms + target_ms // 2 <= value <= min(duration_ms, ideal + SAFE_BOUNDARY_SEARCH_MS)
                    ]
                end_ms = min(candidates, key=lambda value: abs(value - ideal)) if candidates else ideal
            if end_ms <= start_ms:
                end_ms = min(duration_ms, start_ms + target_ms)
            units.append(
                {
                    "unit_id": f"{source_id}:{start_ms}:{end_ms}",
                    "source_id": source_id,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "evidence_start_ms": max(0, start_ms - DEFAULT_WINDOW_OVERLAP_MS),
                    "evidence_end_ms": min(duration_ms, end_ms + DEFAULT_WINDOW_OVERLAP_MS),
                    "clock": f"{_clock(start_ms)}-{_clock(end_ms)}",
                    "activity_episode_ids": [
                        str(item.get("episode_id"))
                        for item in activity_episodes
                        if item.get("episode_id")
                        and _items_overlap(
                            item, {"start_ms": start_ms, "end_ms": end_ms}
                        )
                    ],
                }
            )
            start_ms = end_ms
    for ordinal, unit in enumerate(units, start=1):
        unit["ordinal"] = ordinal
    return units








def _load_project_source_transcript(source: dict[str, Any]) -> list[TranscriptEvidence]:
    output = source.get("stages", {}).get("transcription", {}).get("output")
    if not isinstance(output, dict):
        return []
    timing_path = Path(str(output.get("timing_path") or ""))
    text_path = Path(str(output.get("text_path") or ""))
    try:
        texts = []
        for line in text_path.read_text(encoding="utf-8").splitlines():
            _, separator, text = line.partition(". ")
            texts.append(text if separator else line)
        with timing_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error):
        return []
    result = []
    for row, text in zip(rows, texts):
        try:
            start_ms = round(float(row["start"]) * 1000)
            end_ms = round(float(row["end"]) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        if text.strip() and end_ms > start_ms:
            result.append(TranscriptEvidence(start_ms, end_ms, text.strip()))
    return result




def _items_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return int(left.get("end_ms", 0)) > int(right.get("start_ms", 0)) and int(
        left.get("start_ms", 0)
    ) < int(right.get("end_ms", 0))




def _compact_event_graph(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nodes = [
        {
            "event_id": item.get("event_id"),
            "start_ms": item.get("start_ms"),
            "end_ms": item.get("end_ms"),
            "observed_label": str(
                item.get("observed_label") or item.get("visual_state") or ""
            )[:80],
            "visual_category": item.get("visual_category"),
            "possible_interruption": bool(item.get("possible_interruption")),
            "activity_episode_ids": _string_list(item.get("activity_episode_ids")),
        }
        for item in _object_list(value.get("nodes"))
    ]
    node_ids = {str(item.get("event_id")) for item in nodes}
    edges = [
        item
        for item in _object_list(value.get("edges"))
        if str(item.get("from_event_id")) in node_ids
        and str(item.get("to_event_id")) in node_ids
    ]
    return {"nodes": nodes, "edges": edges}










def build_editorial_prompt(
    *,
    source_id: str,
    source_duration_ms: int,
    window_index: int,
    window_start_ms: int,
    window_end_ms: int,
    title_or_game: str,
    objective: str,
    transcript: Sequence[TranscriptEvidence],
    visuals: Sequence[VisualEvidence],
    cumulative_context: dict[str, Any],
    acoustic_events: Sequence[dict[str, Any]] = (),
    temporal_bursts: Sequence[dict[str, Any]] = (),
    game_knowledge: str = "",
    output_locale: str = "en",
    evidence_start_ms: int | None = None,
    evidence_end_ms: int | None = None,
) -> str:
    transcript_payload = [asdict(item) for item in transcript]
    visual_payload = [asdict(item) for item in visuals]
    evidence_start = window_start_ms if evidence_start_ms is None else evidence_start_ms
    evidence_end = window_end_ms if evidence_end_ms is None else evidence_end_ms
    return f"""Task: create a factual event-and-meaning map for one chronological source window.

Output language: {output_language_instruction(output_locale)}

Project title/game: {title_or_game}
Project objective: {objective}
Source ID: {source_id}
Source duration ms: {source_duration_ms}
Core output window: {window_index} ({window_start_ms}-{window_end_ms} ms)
Overlapping evidence window: {evidence_start}-{evidence_end} ms
Factual mapping rules:
- Produce observations only. Do not recommend cuts, preservation, narration, montage, effects, target lengths, or other editorial treatments.
- The processing window is not a scene boundary. Use overlap evidence to understand events crossing an edge, but keep every returned range inside the core output window.
- Treat the complete transcript as the spoken evidence backbone and sampled frames as visual evidence. Neither modality may silently override the other.
- Track concrete activities, decisions, discoveries, attempts, setbacks, changed strategies, interruptions, returns, mechanics, locations, characters, and outcomes.
- Visual labels for victory, defeat, completion, failure, causality, or identity remain hypotheses unless transcript evidence, explicit result-screen semantics, or later continuity corroborates them.
- Silence is not an event classification. A silent range may still contain concentration, action, atmosphere, a cutscene, or an important nonverbal reaction.
- Semantic spans describe one coherent factual development or spoken topic. Do not bundle sequential A/B/C events merely because they share a theme.
- Use short evidence-specific labels and summaries suitable for a human editor's timeline. Store long-horizon significance in the cumulative context rather than inflating local labels.
- Do not use hardcoded knowledge of any particular game, creator, or reference project.
- Return at most 36 semantic spans. Empty factual spans are valid when the supplied evidence establishes nothing specific.

Prior cumulative context (may originate in an earlier window or source):
{json.dumps(cumulative_context, ensure_ascii=False, separators=(',', ':'))}

Time-aligned transcript evidence:
{json.dumps(transcript_payload, ensure_ascii=False, separators=(',', ':'))}

Sampled visual evidence:
{json.dumps(visual_payload, ensure_ascii=False, separators=(',', ':'))}

Local acoustic emphasis cues:
{json.dumps(list(acoustic_events), ensure_ascii=False, separators=(',', ':'))}

Five-frame transition-burst findings:
{json.dumps(list(temporal_bursts), ensure_ascii=False, separators=(',', ':'))}

Bounded reusable knowledge for this game/title (may contain explicitly marked uncertainty):
{game_knowledge}

Completion: account for the complete core output window before responding. Every returned range is contained
within that core window, every claim is grounded in supplied evidence, and every schema field is complete.
Return only the JSON object required by the response schema.
"""


def merge_cumulative_context(
    existing: dict[str, Any], update: dict[str, Any]
) -> dict[str, list[Any]]:
    merged = _normalized_context(existing)
    normalized_update = _normalized_context(update)
    for field in CONTEXT_FIELDS:
        seen = {_context_key(item) for item in merged[field]}
        for item in normalized_update[field]:
            key = _context_key(item)
            if key not in seen:
                merged[field].append(item)
                seen.add(key)
        merged[field] = merged[field][-200:]
    return merged


def _parse_response(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3]
    try:
        parsed = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise SubtitlerError("Editorial analysis returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise SubtitlerError("Editorial analysis response must be a JSON object")
    return parsed


def _normalize_window_result(
    value: dict[str, Any],
    *,
    source_id: str,
    window_index: int,
    window_start_ms: int,
    window_end_ms: int,
) -> dict[str, Any]:
    return {
        "window_index": window_index,
        "start_ms": window_start_ms,
        "end_ms": window_end_ms,
        "summary": _text(value.get("summary")),
        "context_update": _normalized_context(
            value.get("context_update")
            if isinstance(value.get("context_update"), dict)
            else {}
        ),
        "semantic_spans": _normalize_timed_items(
            value.get("semantic_spans"),
            source_id,
            "span",
            window_index,
            window_start_ms,
            window_end_ms,
        ),
    }












def _normalize_timed_items(
    value: Any,
    source_id: str,
    kind: str,
    window_index: int,
    window_start_ms: int,
    window_end_ms: int,
) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(_object_list(value)):
        start_ms, end_ms = _bounded_range(item, window_start_ms, window_end_ms)
        normalized = dict(item)
        normalized.update(
            {
                "id": f"{source_id}:{kind}:{window_index:04d}:{index:04d}",
                "source_id": source_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
        if "confidence" in normalized:
            normalized["confidence"] = _confidence(normalized.get("confidence"))
        result.append(normalized)
    return result


def _bounded_range(item: dict[str, Any], lower: int, upper: int) -> tuple[int, int]:
    start = max(lower, min(upper, _integer(item.get("start_ms"), lower)))
    end = max(start + 1, min(upper, _integer(item.get("end_ms"), start + 1)))
    if start >= upper:
        start = max(lower, upper - 1)
        end = upper
    return start, end


def _normalized_context(value: dict[str, Any]) -> dict[str, list[Any]]:
    return {
        field: list(value.get(field))[:200] if isinstance(value.get(field), list) else []
        for field in CONTEXT_FIELDS
    }


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][:2000]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)][:200]


def _text(value: Any) -> str:
    return str(value or "").strip()[:8000]


def _integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default




def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _context_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).casefold()


def _clock(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"






































def _valid_final_range(item: dict[str, Any], duration_ms: int) -> tuple[int, int] | None:
    raw_start = _integer(item.get("start_ms"), -1)
    raw_end = _integer(item.get("end_ms"), -1)
    if raw_start < 0 or raw_end <= raw_start:
        return None
    start_ms = min(duration_ms, raw_start)
    end_ms = min(duration_ms, raw_end)
    if end_ms <= start_ms:
        return None
    return start_ms, end_ms
