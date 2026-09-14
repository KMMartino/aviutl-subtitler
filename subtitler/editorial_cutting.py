"""Deterministic human editing guides for long recordings."""

from __future__ import annotations

from typing import Any
from .errors import SubtitlerError
from .speech_gaps import SpeechGapPolicy, find_speech_gaps


HUMAN_INFORMATION_PROMPT_VERSION = "human-information-v3"
VOICE_GAP_MIN_MS = 2_000
VOICE_LEADING_HANDLE_MS = 50
VOICE_TRAILING_HANDLE_MS = 100


def build_human_information_plan(
    *, project: dict[str, Any], synthesis: dict[str, Any],
    speech_activity: dict[str, list[tuple[int, int]]],
    settings: dict[str, Any] | None = None,
    voice_energy: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create deterministic voice-gap guides around a factual narration map."""
    sources = {
        str(item.get("source_id")): item
        for item in project.get("sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    settings = settings or {}
    leading = float(settings.get('voice_leading_handle_ms', VOICE_LEADING_HANDLE_MS))
    trailing = float(settings.get('voice_trailing_handle_ms', VOICE_TRAILING_HANDLE_MS))
    minimum = float(settings.get('voice_gap_min_ms', VOICE_GAP_MIN_MS))
    adaptive_edges = settings.get('gap_edge_mode', 'acoustic') == 'acoustic'
    cuts: list[dict[str, Any]] = []
    for source_id, source in sources.items():
        result = source.get("result") if isinstance(source.get("result"), dict) else {}
        if source_id not in speech_activity:
            raise SubtitlerError(f"Voice-gap planning requires detected speech for {source_id}")
        # Acoustic evidence protects words that transcript segmentation omitted;
        # transcript ranges conservatively protect speech the detector missed.
        ranges = [*speech_activity[source_id], *[
            (int(item.get("start_ms", 0)), int(item.get("end_ms", 0)))
            for item in (result.get("speech_segments") or result.get("utterance_groups") or [])
            if isinstance(item, dict)
        ]]
        policy = SpeechGapPolicy(leading / 1000, trailing / 1000,
                                 0, 0 if adaptive_edges else minimum / 1000, include_edges=True)
        for gap in find_speech_gaps(((start / 1000, end / 1000) for start, end in ranges), policy,
                                   duration=int(source.get("duration_ms", 0)) / 1000):
            start, end = gap.cut_start, gap.cut_end
            edge_evidence: dict[str, Any] = {'method': 'fixed_handles', 'leading_ms': leading, 'trailing_ms': trailing}
            if adaptive_edges:
                from .acoustic_edges import refine_gap
                if voice_energy is not None and source_id in voice_energy:
                    start, end, edge_evidence = refine_gap(gap, voice_energy[source_id], duration=int(source['duration_ms']) / 1000)
                else:
                    edge_evidence['fallback'] = 'voice_energy_unavailable'
            if round(end * 1000) - round(start * 1000) < minimum:
                continue
            cuts.append({
                "cut_id": f"cut-{len(cuts) + 1:05d}", "source_id": source_id,
                "start_ms": round(start * 1000), "end_ms": round(end * 1000),
                "candidate_kind": "voice_free_gap", "confidence": 1.0,
                "internal_reason": "Detected speech gap with deterministic edge protection; review visual content before applying.",
                "edge_evidence": edge_evidence,
            })

    narration_actions = narration_actions_from_briefs(synthesis.get("narration_briefs", []))
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


def narration_actions_from_briefs(briefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    narration_actions: list[dict[str, Any]] = []
    for brief in briefs:
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
                "instruction": str(brief.get("purpose") or ""),
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
                    "evidence_notes": list(brief.get("evidence_notes", [])),
                    "talking_points": list(brief.get("talking_points", [])),
                    "representative_visuals": list(
                        brief.get("representative_visuals", [])
                    ),
                },
            }
        )
    return narration_actions
