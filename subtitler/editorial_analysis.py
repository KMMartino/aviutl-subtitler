"""Hosted, suggestion-only semantic analysis for one long-form source at a time."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, Sequence

from .errors import StructuredOutputIncompleteError, SubtitlerError
from .editorial_locale import locale_label, output_language_instruction


EDITORIAL_PROMPT_VERSION = "editorial-map-v4"
DEFAULT_WINDOW_MS = 45 * 60 * 1000
EDITORIAL_OUTPUT_MAX_TOKENS = 16_384
MIN_OVERFLOW_SPLIT_MS = 8 * 60 * 1000
MAX_OVERFLOW_SPLIT_DEPTH = 3
VALID_DISPOSITIONS = {"keep", "condense", "omit", "connect", "review"}
VALID_PRESENTATIONS = {
    "live",
    "live_excerpt",
    "narration_over_source",
    "narration_montage",
    "narration_bridge",
}
VALID_CREATIVE_TYPES = {
    "punch_in",
    "visual_gag",
    "freeze_frame",
    "reaction_replay",
    "emphasis_text",
    "sound_design",
    "other",
}
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
_BOOLEAN = {"type": "boolean"}
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
        "recommendations": _array(
            _strict_object(
                {
                    **_TIMED_BASE,
                    "disposition": {"type": "string", "enum": sorted(VALID_DISPOSITIONS)},
                    "presentation_mode": {"type": "string", "enum": sorted(VALID_PRESENTATIONS)},
                    "reason": _STRING,
                    "viewer_benefit": _STRING,
                    "confidence": _NUMBER,
                    "continuity_case": _STRING,
                    "subtraction_case": _STRING,
                    "selection_case": _STRING,
                    "context_dependencies": _STRING_ARRAY,
                    "evidence_refs": _STRING_ARRAY,
                    "head_handle_ms": _INTEGER,
                    "tail_handle_ms": _INTEGER,
                    "estimated_kept_min_ms": _INTEGER,
                    "estimated_kept_max_ms": _INTEGER,
                }
            )
        ),
        "narration_briefs": _array(
            _strict_object(
                {
                    **_TIMED_BASE,
                    "purpose": _STRING,
                    "memory_jog": _STRING,
                    "talking_points": _STRING_ARRAY,
                    "interpretation_or_foreshadowing": _STRING_ARRAY,
                    "representative_visuals": _STRING_ARRAY,
                    "live_audio_anchors": _STRING_ARRAY,
                    "uncertainties": _STRING_ARRAY,
                    "estimated_spoken_min_ms": _INTEGER,
                    "estimated_spoken_max_ms": _INTEGER,
                    "evidence_refs": _STRING_ARRAY,
                }
            )
        ),
        "creative_suggestions": _array(
            _strict_object(
                {
                    **_TIMED_BASE,
                    "type": {"type": "string", "enum": sorted(VALID_CREATIVE_TYPES)},
                    "suggestion": _STRING,
                    "backup_option": _STRING,
                    "trigger": _STRING,
                    "asset_idea": _STRING,
                    "confidence": _NUMBER,
                    "evidence_refs": _STRING_ARRAY,
                }
            )
        ),
        "emphasized_phrases": _array(
            _strict_object(
                {
                    **_TIMED_BASE,
                    "exact_phrase": _STRING,
                    "reason": _STRING,
                    "confidence": _NUMBER,
                    "evidence_refs": _STRING_ARRAY,
                }
            )
        ),
        "connections": _array(
            _strict_object(
                {
                    "from_ref": _STRING,
                    "to_ref": _STRING,
                    "relationship": _STRING,
                    "confidence": _NUMBER,
                    "editorial_use": _STRING,
                }
            )
        ),
    }
)

GLOBAL_EDITORIAL_RESPONSE_SCHEMA = _strict_object(
    {
        "global_threads": _array(
            _strict_object(
                {"title": _STRING, "summary": _STRING, "recommendation_ids": _STRING_ARRAY}
            )
        ),
        "connections": _array(
            _strict_object(
                {
                    "from_ref": _STRING,
                    "to_ref": _STRING,
                    "relationship": _STRING,
                    "editorial_use": _STRING,
                    "confidence": _NUMBER,
                }
            )
        ),
        "conflicts": _array(
            _strict_object(
                {
                    "recommendation_ids": _STRING_ARRAY,
                    "reason": _STRING,
                    "suggested_resolution": _STRING,
                }
            )
        ),
        "duration_budget": _strict_object(
            {
                "source_total_ms": _INTEGER,
                "target_min_ms": _INTEGER,
                "target_max_ms": _INTEGER,
                "continuity_led_estimated_ms": _INTEGER,
                "selection_led_estimated_ms": _INTEGER,
                "lower_bound_safe": _BOOLEAN,
                "upper_bound_safe": _BOOLEAN,
                "warning": _STRING,
            }
        ),
        "editorial_blend_summary": _STRING,
        "continuity_led_plan": _array(
            _strict_object(
                {"recommendation_id": _STRING, "priority": _INTEGER, "reason": _STRING}
            )
        ),
        "selection_led_plan": _array(
            _strict_object(
                {"recommendation_id": _STRING, "priority": _INTEGER, "reason": _STRING}
            )
        ),
    }
)

DIRECTOR_REVIEW_RESPONSE_SCHEMA = _strict_object(
    {
        "executive_direction": _STRING,
        "pacing_assessment": _STRING,
        "intrigue_assessment": _STRING,
        "information_density_assessment": _STRING,
        "continuity_assessment": _STRING,
        "priority_changes": _array(
            _strict_object(
                {
                    "recommendation_id": _STRING,
                    "priority": _INTEGER,
                    "action": _STRING,
                    "rationale": _STRING,
                }
            )
        ),
        "protected_moments": _array(
            _strict_object(
                {"recommendation_id": _STRING, "rationale": _STRING}
            )
        ),
        "unresolved_questions": _STRING_ARRAY,
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
    must_keep_notes: Sequence[str] = (),
    de_emphasize_notes: Sequence[str] = (),
    acoustic_events: Sequence[dict[str, Any]] = (),
    temporal_bursts: Sequence[dict[str, Any]] = (),
    game_knowledge: str = "",
    window_ms: int = DEFAULT_WINDOW_MS,
    progress: Callable[[str], None] | None = None,
    completed_windows: Sequence[dict[str, Any]] = (),
    window_completed: Callable[[dict[str, Any]], None] | None = None,
    output_locale: str = "en",
) -> dict[str, Any]:
    """Analyze a source serially while carrying ordinary cross-window/project state.

    The same cumulative context shape is used for the next window and the next
    source file. File boundaries therefore have no special editorial meaning.
    """
    if not source_id.strip() or not title_or_game.strip() or not objective.strip():
        raise SubtitlerError("Editorial source ID, title/game, and objective are required")
    if source_duration_ms <= 0 or window_ms <= 0:
        raise SubtitlerError("Editorial source and analysis-window durations must be positive")
    context = _normalized_context(cumulative_context or {})
    windows: list[dict[str, Any]] = []
    all_recommendations: list[dict[str, Any]] = []
    all_spans: list[dict[str, Any]] = []
    all_narration: list[dict[str, Any]] = []
    all_connections: list[dict[str, Any]] = []
    all_creative: list[dict[str, Any]] = []
    all_emphasis: list[dict[str, Any]] = []
    cached_windows = {
        int(item.get("base_window_index", -1)): item
        for item in completed_windows
        if isinstance(item, dict)
    }
    start_ms = 0
    base_window_index = 0
    window_count = math.ceil(source_duration_ms / window_ms)
    while start_ms < source_duration_ms:
        end_ms = min(source_duration_ms, start_ms + window_ms)
        cached = cached_windows.get(base_window_index)
        if _valid_cached_window(cached, start_ms, end_ms):
            normalized_windows = [
                dict(item) for item in cached["windows"] if isinstance(item, dict)
            ]
            context = _normalized_context(cached["cumulative_context_after"])
            if progress is not None:
                progress(
                    locale_label(
                        output_locale,
                        f"reusing completed window {base_window_index + 1}/{window_count} "
                        f"({_clock(start_ms)}-{_clock(end_ms)}).",
                        f"完了済みウィンドウ {base_window_index + 1}/{window_count} を再利用 "
                        f"({_clock(start_ms)}-{_clock(end_ms)})。",
                    )
                )
        else:
            if progress is not None:
                progress(
                    locale_label(
                        output_locale,
                        f"mapping window {base_window_index + 1}/{window_count} "
                        f"({_clock(start_ms)}-{_clock(end_ms)})...",
                        f"ウィンドウ {base_window_index + 1}/{window_count} をマッピング中 "
                        f"({_clock(start_ms)}-{_clock(end_ms)})…",
                    )
                )
            normalized_windows, context = _analyze_range_with_overflow_recovery(
                provider=provider,
                source_id=source_id,
                source_duration_ms=source_duration_ms,
                title_or_game=title_or_game,
                objective=objective,
                transcript=transcript,
                visuals=visuals,
                cumulative_context=context,
                must_keep_notes=must_keep_notes,
                de_emphasize_notes=de_emphasize_notes,
                acoustic_events=acoustic_events,
                temporal_bursts=temporal_bursts,
                game_knowledge=game_knowledge,
                output_locale=output_locale,
                window_start_ms=start_ms,
                window_end_ms=end_ms,
                analysis_index=base_window_index * 100,
                progress=progress,
            )
            if window_completed is not None:
                window_completed(
                    {
                        "base_window_index": base_window_index,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "windows": normalized_windows,
                        "cumulative_context_after": context,
                    }
                )
        for normalized in normalized_windows:
            windows.append(normalized)
            all_spans.extend(normalized["semantic_spans"])
            all_recommendations.extend(normalized["recommendations"])
            all_narration.extend(normalized["narration_briefs"])
            all_connections.extend(normalized["connections"])
            all_creative.extend(normalized["creative_suggestions"])
            all_emphasis.extend(normalized["emphasized_phrases"])
        if progress is not None:
            progress(
                locale_label(
                    output_locale,
                    f"window {base_window_index + 1}/{window_count} complete: "
                    f"{sum(len(item['recommendations']) for item in normalized_windows)} direction(s), "
                    f"{sum(len(item['narration_briefs']) for item in normalized_windows)} narration brief(s), "
                    f"{sum(len(item['creative_suggestions']) for item in normalized_windows)} creative accent(s).",
                    f"ウィンドウ {base_window_index + 1}/{window_count} が完了: 編集方針 "
                    f"{sum(len(item['recommendations']) for item in normalized_windows)} 件、ナレーション案 "
                    f"{sum(len(item['narration_briefs']) for item in normalized_windows)} 件、演出案 "
                    f"{sum(len(item['creative_suggestions']) for item in normalized_windows)} 件。",
                )
            )
        base_window_index += 1
        start_ms = end_ms

    all_creative = deduplicate_creative_suggestions(all_creative)
    return {
        "prompt_version": EDITORIAL_PROMPT_VERSION,
        "source_id": source_id,
        "source_duration_ms": source_duration_ms,
        "windows": windows,
        "semantic_spans": all_spans,
        "recommendations": all_recommendations,
        "narration_briefs": all_narration,
        "connections": all_connections,
        "creative_suggestions": all_creative,
        "emphasized_phrases": all_emphasis,
        "timeline_coverage": build_timeline_coverage(source_duration_ms, all_recommendations),
        "cumulative_context": context,
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
    must_keep_notes: Sequence[str],
    de_emphasize_notes: Sequence[str],
    acoustic_events: Sequence[dict[str, Any]],
    temporal_bursts: Sequence[dict[str, Any]],
    game_knowledge: str,
    output_locale: str,
    window_start_ms: int,
    window_end_ms: int,
    analysis_index: int,
    progress: Callable[[str], None] | None,
    split_depth: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    transcript_window = [
        item
        for item in transcript
        if item.end_ms > window_start_ms and item.start_ms < window_end_ms
    ]
    visual_window = [
        item
        for item in visuals
        if item.end_ms > window_start_ms and item.start_ms < window_end_ms
    ]
    local_acoustic = [
        item
        for item in acoustic_events
        if int(item.get("end_ms", 0)) > window_start_ms
        and int(item.get("start_ms", 0)) < window_end_ms
    ]
    local_bursts = [
        item
        for item in temporal_bursts
        if window_start_ms <= int(item.get("timestamp_ms", 0)) < window_end_ms
    ]
    prompt = build_editorial_prompt(
        source_id=source_id,
        source_duration_ms=source_duration_ms,
        window_index=analysis_index,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        title_or_game=title_or_game,
        objective=objective,
        transcript=transcript_window,
        visuals=visual_window,
        cumulative_context=cumulative_context,
        must_keep_notes=must_keep_notes,
        de_emphasize_notes=de_emphasize_notes,
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
            must_keep_notes=must_keep_notes,
            de_emphasize_notes=de_emphasize_notes,
            acoustic_events=acoustic_events,
            temporal_bursts=temporal_bursts,
            game_knowledge=game_knowledge,
            output_locale=output_locale,
            window_start_ms=window_start_ms,
            window_end_ms=split_ms,
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
            must_keep_notes=must_keep_notes,
            de_emphasize_notes=de_emphasize_notes,
            acoustic_events=acoustic_events,
            temporal_bursts=temporal_bursts,
            game_knowledge=game_knowledge,
            output_locale=output_locale,
            window_start_ms=split_ms,
            window_end_ms=window_end_ms,
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


def _valid_cached_window(value: Any, start_ms: int, end_ms: int) -> bool:
    return (
        isinstance(value, dict)
        and int(value.get("start_ms", -1)) == start_ms
        and int(value.get("end_ms", -1)) == end_ms
        and isinstance(value.get("windows"), list)
        and bool(value["windows"])
        and isinstance(value.get("cumulative_context_after"), dict)
    )


def reconcile_editorial_project(
    *,
    provider: EditorialPlanningProvider,
    project: dict[str, Any],
) -> dict[str, Any]:
    """Build two coherent, non-destructive plans at the requested duration bounds."""
    recommendations = [
        _compact_recommendation(item)
        for item in project.get("editorial_map", {}).get("recommendations", [])
        if isinstance(item, dict)
    ][:1500]
    source_summaries = []
    for source in sorted(project.get("sources", []), key=lambda item: item.get("order", 0)):
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        semantic = source.get("stages", {}).get("semantic_spans", {}).get("output")
        windows = semantic.get("windows", []) if isinstance(semantic, dict) else []
        source_summaries.append(
            {
                "source_id": source.get("source_id"),
                "order": source.get("order"),
                "duration_ms": source.get("duration_ms"),
                "window_summaries": [
                    str(item.get("summary") or "")[:1000]
                    for item in windows
                    if isinstance(item, dict) and str(item.get("summary") or "").strip()
                ],
                "semantic_span_count": len(result.get("semantic_spans", [])) if isinstance(result, dict) else 0,
                "timeline_coverage": result.get("timeline_coverage", []) if isinstance(result, dict) else [],
            }
        )
    language_rule = output_language_instruction(project.get("output_locale", "en"))
    prompt = f"""Task: reconcile a completed multi-source editorial analysis into two suggestion-only project plans.

Output language: {language_rule}

Title/game: {project['title_or_game']}
Objective: {project['objective']}
Total source duration ms: {sum(int(item['duration_ms']) for item in project['sources'])}
Requested lower final duration ms: {project['target_duration_min_ms']}
Requested upper final duration ms: {project['target_duration_max_ms']}
Must-keep notes: {json.dumps(project.get('must_keep_notes', []), ensure_ascii=False)}
Subjects to de-emphasize: {json.dumps(project.get('de_emphasize_notes', []), ensure_ascii=False)}

Rules:
- This is a plan only. Never claim that media has been cut, moved, or rendered.
- The upper-bound plan is continuity-led: start intact, protect texture and meaningful silence, and justify removals.
- The lower-bound plan is selection-led: choose the material that earns inclusion and add required context or narration.
- Do not apply uniform compression. Blend the priors per scene and thread.
- Silence alone is never evidence for removal.
- File boundaries have no editorial meaning. Connect related moments across them normally.
- Protect causality, unique payoffs, explicit must-keeps, and uncertain quiet material.
- If a requested bound is unsafe, say so rather than inventing a feasible plan.
- Refer only to recommendation IDs supplied below.
- Timeline coverage is gap-free. Ranges marked leave_as_is have no edit marker and remain intact in both plans unless a future analysis creates a supported recommendation; do not silently count those ranges as removed.

Ordered source summaries:
{json.dumps(source_summaries, ensure_ascii=False, separators=(',', ':'))}

Candidate recommendations:
{json.dumps(recommendations, ensure_ascii=False, separators=(',', ':'))}

Return one JSON object only:
{{
  "global_threads": [{{"title":"", "summary":"", "recommendation_ids":[]}}],
  "connections": [{{"from_ref":"", "to_ref":"", "relationship":"", "editorial_use":"", "confidence":0.0}}],
  "conflicts": [{{"recommendation_ids":[], "reason":"", "suggested_resolution":""}}],
  "duration_budget": {{
    "source_total_ms": 0, "target_min_ms": 0, "target_max_ms": 0,
    "continuity_led_estimated_ms": 0, "selection_led_estimated_ms": 0,
    "lower_bound_safe": true, "upper_bound_safe": true, "warning": ""
  }},
  "editorial_blend_summary": "explain how different scenes use the two priors",
  "continuity_led_plan": [{{"recommendation_id":"", "priority":0, "reason":""}}],
  "selection_led_plan": [{{"recommendation_id":"", "priority":0, "reason":""}}]
}}
"""
    parsed = _parse_response(
        provider.complete_structured(
            prompt,
            max_tokens=EDITORIAL_OUTPUT_MAX_TOKENS,
            operation="editorial_global",
            response_schema=GLOBAL_EDITORIAL_RESPONSE_SCHEMA,
        )
    )
    known_ids = {item["id"] for item in recommendations if item.get("id")}
    duration = parsed.get("duration_budget") if isinstance(parsed.get("duration_budget"), dict) else {}
    return {
        "global_threads": _object_list(parsed.get("global_threads")),
        "connections": _object_list(parsed.get("connections")),
        "conflicts": _object_list(parsed.get("conflicts")),
        "duration_budget": {
            "source_total_ms": sum(int(item["duration_ms"]) for item in project["sources"]),
            "target_min_ms": int(project["target_duration_min_ms"]),
            "target_max_ms": int(project["target_duration_max_ms"]),
            "continuity_led_estimated_ms": _non_negative_int(duration.get("continuity_led_estimated_ms")),
            "selection_led_estimated_ms": _non_negative_int(duration.get("selection_led_estimated_ms")),
            "lower_bound_safe": bool(duration.get("lower_bound_safe", False)),
            "upper_bound_safe": bool(duration.get("upper_bound_safe", False)),
            "warning": _text(duration.get("warning")),
        },
        "editorial_blend_summary": _text(parsed.get("editorial_blend_summary")),
        "continuity_led_plan": _normalize_plan(parsed.get("continuity_led_plan"), known_ids),
        "selection_led_plan": _normalize_plan(parsed.get("selection_led_plan"), known_ids),
    }


def review_editorial_project(
    *,
    provider: EditorialPlanningProvider,
    project: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    """Perform a separate project-wide director review without mutating edit suggestions."""
    recommendations = [
        _compact_recommendation(item)
        for item in project.get("editorial_map", {}).get("recommendations", [])
        if isinstance(item, dict)
    ][:1200]
    source_summaries = []
    for source in sorted(project.get("sources", []), key=lambda item: item.get("order", 0)):
        semantic = source.get("stages", {}).get("semantic_spans", {}).get("output")
        windows = semantic.get("windows", []) if isinstance(semantic, dict) else []
        source_summaries.append(
            {
                "source_id": source.get("source_id"),
                "order": source.get("order"),
                "duration_ms": source.get("duration_ms"),
                "window_summaries": [
                    str(item.get("summary") or "")[:800]
                    for item in windows
                    if isinstance(item, dict) and str(item.get("summary") or "").strip()
                ],
            }
        )
    narration = [
        {
            "id": item.get("id"),
            "source_id": item.get("source_id"),
            "start_ms": item.get("start_ms"),
            "end_ms": item.get("end_ms"),
            "purpose": str(item.get("purpose") or "")[:500],
        }
        for item in project.get("editorial_map", {}).get("narration_briefs", [])
        if isinstance(item, dict)
    ][:500]
    creative = [
        {
            "id": item.get("id"),
            "source_id": item.get("source_id"),
            "start_ms": item.get("start_ms"),
            "end_ms": item.get("end_ms"),
            "type": item.get("type"),
            "suggestion": str(item.get("suggestion") or "")[:500],
        }
        for item in project.get("editorial_map", {}).get("creative_suggestions", [])
        if isinstance(item, dict)
    ][:500]
    language_rule = output_language_instruction(project.get("output_locale", "en"))
    prompt = f"""Task: act as the final director reviewing a suggestion-only long-form edit plan.

Output language: {language_rule}

Title/game: {project['title_or_game']}
Recording objective: {project['objective']}
Requested final duration: {project['target_duration_min_ms']}-{project['target_duration_max_ms']} ms
Total source duration: {sum(int(item['duration_ms']) for item in project['sources'])} ms

Judge the proposed project as one viewer experience, not as isolated clips. Assess:
- pacing across the complete arc, including whether long intact stretches are earned;
- intrigue, escalation, setup/payoff, and whether important questions remain alive;
- information density, including exhausting repetition or narration overload;
- continuity and causality across source files and distant callbacks.

Rules:
- This is a final advisory review. Do not claim edits have been applied.
- Preserve meaningful silence, difficult gameplay, atmosphere, and earned duration.
- Do not manufacture issues merely to fill fields. Concise positive assessments are valid.
- Refer only to supplied recommendation IDs in priority_changes and protected_moments.
- Prioritize no more than 12 changes and protect no more than 12 moments.
- Treat the existing reconciliation as evidence, not an instruction that must be endorsed.

Source summaries:
{json.dumps(source_summaries, ensure_ascii=False, separators=(',', ':'))}

Existing project synthesis:
{json.dumps(reconciliation, ensure_ascii=False, separators=(',', ':'))}

Candidate recommendations:
{json.dumps(recommendations, ensure_ascii=False, separators=(',', ':'))}

Narration opportunities:
{json.dumps(narration, ensure_ascii=False, separators=(',', ':'))}

Creative accents:
{json.dumps(creative, ensure_ascii=False, separators=(',', ':'))}

Return the requested JSON object only.
"""
    parsed = _parse_response(
        provider.complete_structured(
            prompt,
            max_tokens=8192,
            operation="editorial_director",
            response_schema=DIRECTOR_REVIEW_RESPONSE_SCHEMA,
        )
    )
    known_ids = {item.get("id") for item in recommendations if item.get("id")}
    priority_changes = []
    for item in _object_list(parsed.get("priority_changes")):
        recommendation_id = str(item.get("recommendation_id") or "")
        if recommendation_id not in known_ids:
            continue
        priority_changes.append(
            {
                "recommendation_id": recommendation_id,
                "priority": _non_negative_int(item.get("priority")),
                "action": _text(item.get("action")),
                "rationale": _text(item.get("rationale")),
            }
        )
    protected_moments = []
    for item in _object_list(parsed.get("protected_moments")):
        recommendation_id = str(item.get("recommendation_id") or "")
        if recommendation_id in known_ids:
            protected_moments.append(
                {
                    "recommendation_id": recommendation_id,
                    "rationale": _text(item.get("rationale")),
                }
            )
    return {
        "executive_direction": _text(parsed.get("executive_direction")),
        "pacing_assessment": _text(parsed.get("pacing_assessment")),
        "intrigue_assessment": _text(parsed.get("intrigue_assessment")),
        "information_density_assessment": _text(
            parsed.get("information_density_assessment")
        ),
        "continuity_assessment": _text(parsed.get("continuity_assessment")),
        "priority_changes": priority_changes[:12],
        "protected_moments": protected_moments[:12],
        "unresolved_questions": _string_list(parsed.get("unresolved_questions"))[:12],
    }


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
    must_keep_notes: Sequence[str],
    de_emphasize_notes: Sequence[str],
    acoustic_events: Sequence[dict[str, Any]] = (),
    temporal_bursts: Sequence[dict[str, Any]] = (),
    game_knowledge: str = "",
    output_locale: str = "en",
) -> str:
    transcript_payload = [asdict(item) for item in transcript]
    visual_payload = [asdict(item) for item in visuals]
    return f"""Task: create a suggestion-only editorial map for one chronological source window.

Output language: {output_language_instruction(output_locale)}

Project title/game: {title_or_game}
Project objective: {objective}
Source ID: {source_id}
Source duration ms: {source_duration_ms}
Window: {window_index} ({window_start_ms}-{window_end_ms} ms)
Must-keep notes: {json.dumps(list(must_keep_notes), ensure_ascii=False)}
Subjects to de-emphasize: {json.dumps(list(de_emphasize_notes), ensure_ascii=False)}

Editorial rules:
- Never perform or imply an automatic edit. Produce evidence-backed suggestions only.
- Silence is neutral. It may be concentration, tension, atmosphere, visual action, or dead time. Never recommend a cut because speech is absent.
- Treat source-file boundaries exactly like ordinary distant timeline boundaries.
- Analyze two distinct priors. Continuity-first begins with the recording intact and requires evidence for removal. Selection-first begins empty and requires evidence for inclusion plus necessary context.
- "Value if selected" and "damage if removed" are not opposites. Preserve both judgments.
- Prefer review over omit when evidence is uncertain, especially for visually quiet new material.
- Dynamic handles are soft. Give a preferred range and optional head/tail flexibility.
- Preserve chronology and causality unless explicitly identifying a retrospective, setup, or foreshadowing link.
- Narration does not exist yet. Narration output must be a memory-jogging brief with facts, possible interpretation/foreshadowing, representative visuals, and uncertainties—not a claim that the creator already said it.
- Do not use hardcoded knowledge of any particular game, creator, or reference project.
- Treat acoustic emphasis as an inspection cue, not proof of excitement or laughter.
- Creative accents must be sparse and earned. Suggest punch-ins, freeze/replay, emphasis text, sound design, or a deliberately literal visual gag only when actual words/action support it. Never force a joke.
- Every range not covered by a recommendation is treated as leave-as-is. Add a recommendation for every range that either duration strategy may need to shorten; do not leave intended cuts implicit.
- For emphasized_phrases, exact_phrase must be copied as one contiguous substring from the supplied transcript. Select only phrases that genuinely earn on-screen emphasis.
- Keep every string concise and evidence-specific. Prefer several precise ranges over repeated prose.
- Return at most 30 semantic spans, 36 recommendations, 12 narration briefs, 8 creative suggestions, 12 emphasized phrases, and 12 connections for this window.

Prior cumulative context (may originate in an earlier window or source):
{json.dumps(cumulative_context, ensure_ascii=False, separators=(',', ':'))}

Time-aligned transcript evidence:
{json.dumps(transcript_payload, ensure_ascii=False, separators=(',', ':'))}

Sampled visual evidence:
{json.dumps(visual_payload, ensure_ascii=False, separators=(',', ':'))}

Local acoustic emphasis cues:
{json.dumps(list(acoustic_events), ensure_ascii=False, separators=(',', ':'))}

Three-frame transition-burst findings:
{json.dumps(list(temporal_bursts), ensure_ascii=False, separators=(',', ':'))}

Bounded reusable knowledge for this game/title (may contain explicitly marked uncertainty):
{game_knowledge}

Return one JSON object only with this shape:
{{
  "summary": "short factual window summary",
  "context_update": {{
    "current_objectives": [], "completed_milestones": [], "open_threads": [],
    "recurring_locations_entities_mechanics": [], "known_repetition_patterns": [],
    "creator_stance_and_sentiment": []
  }},
  "semantic_spans": [{{
    "start_ms": 0, "end_ms": 0, "label": "", "kind": "", "summary": "",
    "confidence": 0.0, "evidence_refs": []
  }}],
  "recommendations": [{{
    "start_ms": 0, "end_ms": 0,
    "disposition": "keep|condense|omit|connect|review",
    "presentation_mode": "live|live_excerpt|narration_over_source|narration_montage|narration_bridge",
    "reason": "", "viewer_benefit": "", "confidence": 0.0,
    "continuity_case": "what is damaged or lost if removed",
    "subtraction_case": "what is stale, duplicated, or costly if preserved in full",
    "selection_case": "what earns a place in a shorter construction",
    "context_dependencies": [], "evidence_refs": [],
    "head_handle_ms": 0, "tail_handle_ms": 0,
    "estimated_kept_min_ms": 0, "estimated_kept_max_ms": 0
  }}],
  "narration_briefs": [{{
    "start_ms": 0, "end_ms": 0, "purpose": "", "memory_jog": "",
    "talking_points": [], "interpretation_or_foreshadowing": [],
    "representative_visuals": [], "live_audio_anchors": [], "uncertainties": [],
    "estimated_spoken_min_ms": 0, "estimated_spoken_max_ms": 0,
    "evidence_refs": []
  }}],
  "creative_suggestions": [{{
    "start_ms": 0, "end_ms": 0,
    "type": "punch_in|visual_gag|freeze_frame|reaction_replay|emphasis_text|sound_design|other",
    "suggestion": "specific editor action", "backup_option": "less intrusive alternative",
    "trigger": "the exact action or spoken phrase that earns it", "asset_idea": "optional visual asset",
    "confidence": 0.0, "evidence_refs": []
  }}],
  "emphasized_phrases": [{{
    "start_ms": 0, "end_ms": 0, "exact_phrase": "verbatim transcript substring",
    "reason": "why this phrase earns on-screen emphasis", "confidence": 0.0,
    "evidence_refs": []
  }}],
  "connections": [{{
    "from_ref": "", "to_ref": "", "relationship": "", "confidence": 0.0,
    "editorial_use": ""
  }}]
}}

All ranges must intersect this window. Empty arrays are valid; unsupported suggestions are not.
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
    recommendations = []
    for index, item in enumerate(_object_list(value.get("recommendations"))):
        disposition = str(item.get("disposition") or "review")
        presentation = str(item.get("presentation_mode") or "live_excerpt")
        if disposition not in VALID_DISPOSITIONS:
            disposition = "review"
        if presentation not in VALID_PRESENTATIONS:
            presentation = "live_excerpt"
        start_ms, end_ms = _bounded_range(item, window_start_ms, window_end_ms)
        recommendations.append(
            {
                "id": f"{source_id}:recommendation:{window_index:04d}:{index:04d}",
                "source_id": source_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "disposition": disposition,
                "presentation_mode": presentation,
                "reason": _text(item.get("reason")),
                "viewer_benefit": _text(item.get("viewer_benefit")),
                "confidence": _confidence(item.get("confidence")),
                "continuity_case": _text(item.get("continuity_case")),
                "subtraction_case": _text(item.get("subtraction_case")),
                "selection_case": _text(item.get("selection_case")),
                "context_dependencies": _string_list(item.get("context_dependencies")),
                "evidence_refs": _string_list(item.get("evidence_refs")),
                "head_handle_ms": _non_negative_int(item.get("head_handle_ms")),
                "tail_handle_ms": _non_negative_int(item.get("tail_handle_ms")),
                "estimated_kept_min_ms": _non_negative_int(item.get("estimated_kept_min_ms")),
                "estimated_kept_max_ms": _non_negative_int(item.get("estimated_kept_max_ms")),
            }
        )
    return {
        "window_index": window_index,
        "start_ms": window_start_ms,
        "end_ms": window_end_ms,
        "summary": _text(value.get("summary")),
        "context_update": _normalized_context(value.get("context_update") if isinstance(value.get("context_update"), dict) else {}),
        "semantic_spans": _normalize_timed_items(
            value.get("semantic_spans"), source_id, "span", window_index, window_start_ms, window_end_ms
        ),
        "recommendations": recommendations,
        "narration_briefs": _normalize_timed_items(
            value.get("narration_briefs"), source_id, "narration", window_index, window_start_ms, window_end_ms
        ),
        "connections": _object_list(value.get("connections")),
        "creative_suggestions": _normalize_creative_items(
            value.get("creative_suggestions"), source_id, window_index, window_start_ms, window_end_ms
        ),
        "emphasized_phrases": _normalize_emphasized_phrases(
            value.get("emphasized_phrases"), source_id, window_index, window_start_ms, window_end_ms
        ),
    }


def _normalize_emphasized_phrases(
    value: Any,
    source_id: str,
    window_index: int,
    window_start_ms: int,
    window_end_ms: int,
) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(_object_list(value)):
        phrase = " ".join(str(item.get("exact_phrase") or "").split())[:240]
        confidence = _confidence(item.get("confidence"))
        if not phrase or confidence < 0.7:
            continue
        start_ms, end_ms = _bounded_range(item, window_start_ms, window_end_ms)
        result.append(
            {
                "id": f"{source_id}:emphasis:{window_index:04d}:{index:04d}",
                "source_id": source_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": phrase,
                "text": phrase,
                "reason": _text(item.get("reason")),
                "confidence": confidence,
                "evidence_refs": _string_list(item.get("evidence_refs")),
            }
        )
    return result


def build_timeline_coverage(
    source_duration_ms: int,
    recommendations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produce an explicit, gap-free ledger without creating user-facing markers for gaps."""
    bounded = [
        item
        for item in recommendations
        if 0 <= int(item.get("start_ms", 0)) < int(item.get("end_ms", 0)) <= source_duration_ms
    ]
    boundaries = {0, source_duration_ms}
    for item in bounded:
        boundaries.update((int(item["start_ms"]), int(item["end_ms"])))
    ordered = sorted(boundaries)
    result: list[dict[str, Any]] = []
    for start, end in zip(ordered, ordered[1:]):
        active = sorted(
            str(item.get("id") or "")
            for item in bounded
            if int(item["start_ms"]) < end and int(item["end_ms"]) > start
        )
        status = "suggested" if active else "leave_as_is"
        if result and result[-1]["status"] == status and result[-1]["recommendation_ids"] == active:
            result[-1]["end_ms"] = end
        else:
            result.append(
                {
                    "start_ms": start,
                    "end_ms": end,
                    "status": status,
                    "recommendation_ids": active,
                }
            )
    return result


def deduplicate_creative_suggestions(
    suggestions: Sequence[dict[str, Any]],
    *,
    nearby_ms: int = 30_000,
) -> list[dict[str, Any]]:
    """Keep the strongest of materially equivalent nearby creative accents."""
    selected: list[dict[str, Any]] = []
    ordered = sorted(
        (dict(item) for item in suggestions if isinstance(item, dict)),
        key=lambda item: (-float(item.get("confidence", 0.0)), int(item.get("start_ms", 0))),
    )
    for candidate in ordered:
        words = _creative_terms(candidate)
        duplicate = False
        for existing in selected:
            if candidate.get("source_id") != existing.get("source_id"):
                continue
            if candidate.get("type") != existing.get("type"):
                continue
            if abs(int(candidate.get("start_ms", 0)) - int(existing.get("start_ms", 0))) > nearby_ms:
                continue
            other_words = _creative_terms(existing)
            similarity = len(words & other_words) / max(1, len(words | other_words))
            if similarity >= 0.35 or _ranges_overlap(candidate, existing):
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
    return sorted(selected, key=lambda item: (int(item.get("start_ms", 0)), str(item.get("id", ""))))


def _creative_terms(item: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(item.get(field) or "").casefold()
        for field in ("suggestion", "trigger", "asset_idea")
    )
    return {part.strip(".,!?、。:;()[]{}\"'") for part in text.split() if len(part.strip()) >= 2}


def _ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return int(left.get("start_ms", 0)) < int(right.get("end_ms", 0)) and int(
        right.get("start_ms", 0)
    ) < int(left.get("end_ms", 0))


def _normalize_creative_items(
    value: Any,
    source_id: str,
    window_index: int,
    window_start_ms: int,
    window_end_ms: int,
) -> list[dict[str, Any]]:
    result = []
    visual_gags = 0
    for index, item in enumerate(_object_list(value)):
        creative_type = str(item.get("type") or "other")
        if creative_type not in VALID_CREATIVE_TYPES:
            creative_type = "other"
        confidence = _confidence(item.get("confidence"))
        suggestion = _text(item.get("suggestion"))
        if confidence < 0.6 or not suggestion:
            continue
        if creative_type == "visual_gag":
            visual_gags += 1
            if visual_gags > 2:
                continue
        start_ms, end_ms = _bounded_range(item, window_start_ms, window_end_ms)
        result.append({
            "id": f"{source_id}:creative:{window_index:04d}:{index:04d}",
            "source_id": source_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "type": creative_type,
            "suggestion": suggestion,
            "backup_option": _text(item.get("backup_option")),
            "trigger": _text(item.get("trigger")),
            "asset_idea": _text(item.get("asset_idea")),
            "confidence": confidence,
            "evidence_refs": _string_list(item.get("evidence_refs")),
        })
    return result


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


def _non_negative_int(value: Any) -> int:
    return max(0, _integer(value, 0))


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


def _compact_recommendation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id",
            "source_id",
            "start_ms",
            "end_ms",
            "disposition",
            "presentation_mode",
            "reason",
            "viewer_benefit",
            "confidence",
            "continuity_case",
            "subtraction_case",
            "selection_case",
            "context_dependencies",
            "estimated_kept_min_ms",
            "estimated_kept_max_ms",
        )
    }


def _normalize_plan(value: Any, known_ids: set[str]) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for item in _object_list(value):
        recommendation_id = str(item.get("recommendation_id") or "")
        if recommendation_id not in known_ids or recommendation_id in seen:
            continue
        result.append(
            {
                "recommendation_id": recommendation_id,
                "priority": _non_negative_int(item.get("priority")),
                "reason": _text(item.get("reason")),
            }
        )
        seen.add(recommendation_id)
    return sorted(result, key=lambda item: item["priority"])
