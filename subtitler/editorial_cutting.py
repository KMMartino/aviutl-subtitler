"""Deterministic human editing guides for long recordings."""

from __future__ import annotations

from typing import Any


HUMAN_INFORMATION_PROMPT_VERSION = "human-information-v2"
VOICE_GAP_MIN_MS = 2_000
VOICE_LEADING_HANDLE_MS = 50
VOICE_TRAILING_HANDLE_MS = 100


def build_human_information_plan(
    *, project: dict[str, Any], synthesis: dict[str, Any]
) -> dict[str, Any]:
    """Create deterministic voice-gap guides around a factual narration map."""
    sources = {
        str(item.get("source_id")): item
        for item in project.get("sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    cuts: list[dict[str, Any]] = []
    for source_id, source in sources.items():
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        speech = [
            (
                max(0, int(item.get("start_ms", 0)) - VOICE_LEADING_HANDLE_MS),
                min(
                    int(source.get("duration_ms", 0)),
                    int(item.get("end_ms", 0)) + VOICE_TRAILING_HANDLE_MS,
                ),
            )
            for item in result.get("speech_segments", [])
            if isinstance(item, dict)
            and int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
        ]
        if not speech:
            speech = [
                (
                    max(
                        0,
                        int(item.get("start_ms", 0)) - VOICE_LEADING_HANDLE_MS,
                    ),
                    min(
                        int(source.get("duration_ms", 0)),
                        int(item.get("end_ms", 0)) + VOICE_TRAILING_HANDLE_MS,
                    ),
                )
                for item in result.get("utterance_groups", [])
                if isinstance(item, dict)
                and int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
            ]
        merged_speech: list[tuple[int, int]] = []
        for start_ms, end_ms in sorted(speech):
            if merged_speech and start_ms <= merged_speech[-1][1]:
                merged_speech[-1] = (
                    merged_speech[-1][0],
                    max(merged_speech[-1][1], end_ms),
                )
            else:
                merged_speech.append((start_ms, end_ms))
        cursor = 0
        duration_ms = int(source.get("duration_ms", 0))
        for start_ms, end_ms in [*merged_speech, (duration_ms, duration_ms)]:
            if start_ms - cursor >= VOICE_GAP_MIN_MS:
                cuts.append(
                    {
                        "cut_id": f"cut-{len(cuts) + 1:05d}",
                        "source_id": source_id,
                        "start_ms": cursor,
                        "end_ms": start_ms,
                        "candidate_kind": "voice_free_gap",
                        "confidence": 1.0,
                        "internal_reason": (
                            "No detected voice after a 50 ms leading speech handle "
                            "and a 100 ms trailing speech handle."
                        ),
                    }
                )
            cursor = max(cursor, end_ms)

    narration_actions: list[dict[str, Any]] = []
    for brief in synthesis.get("narration_briefs", []):
        if not isinstance(brief, dict):
            continue
        kind = str(brief.get("kind") or "causal_bridge")
        narration_actions.append(
            {
                "action_id": f"narration-{len(narration_actions) + 1:03d}",
                "action_type": (
                    "narration_bridge" if kind == "causal_bridge" else "narrated_summary"
                ),
                "source_id": str(brief.get("source_id") or ""),
                "start_ms": int(brief.get("start_ms", 0)),
                "end_ms": int(brief.get("end_ms", 0)),
                "instruction": str(brief.get("purpose") or "Narrate this context."),
                "rationale": str(brief.get("memory_jog") or ""),
                "priority": 1,
                "confidence": 1.0,
                "recommendation_ids": [],
                "narration_brief_ids": [str(brief.get("id") or "")],
                "supporting_edit_ids": [],
                "thread_ids": list(brief.get("thread_ids", [])),
                "event_node_ids": [],
                "operation_ranges": [],
                "audio_treatment": "voiceover",
                "narrative_role": "bridge" if kind == "causal_bridge" else "setup",
                "narration_guidance": {
                    "purpose": str(brief.get("purpose") or ""),
                    "vision": str(brief.get("memory_jog") or ""),
                    "talking_points": list(brief.get("talking_points", [])),
                    "representative_visuals": list(
                        brief.get("representative_visuals", [])
                    ),
                },
            }
        )
    total_ms = sum(int(source.get("duration_ms", 0)) for source in sources.values())
    removed_ms = sum(item["end_ms"] - item["start_ms"] for item in cuts)
    return {
        "workflow": "human_information",
        "prompt_version": HUMAN_INFORMATION_PROMPT_VERSION,
        "final_actions": narration_actions,
        "supporting_edits": [],
        "threads": list(synthesis.get("global_threads", [])),
        "story_actions": list(synthesis.get("event_phases", [])),
        "protected_zones": [],
        "cut_candidates": [],
        "confirmed_cuts": cuts,
        "manual_review_count": 0,
        "estimated_final_ms": max(0, total_ms - removed_ms),
        "removed_ms": removed_ms,
        "narration_replaced_ms": 0,
        "plan_audit": {
            "summary": "Cut markers identify voice-free gaps only; the human editor is authoritative.",
            "beat_reviews": [],
            "replanned_unit_count": 0,
            "post_replan_issues": [],
        },
    }
