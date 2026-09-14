"""Shared editorial judgment guidance distilled from preserved reference studies.

These are contextual decision aids, not fixed genre rules or integrity validators.
Provenance identifies archived studies; no runtime access to those files is needed.
"""
from __future__ import annotations

import copy
from typing import Any

EDITORIAL_GUIDANCE: dict[str, Any] = {
    "principles": [
        {
            "id": "viewing_promise",
            "guidance": "Use the creator's explicit purpose to decide what experience the selected footage should provide. Do not infer an audience, genre, or desired compression merely from the game or an example format. Where intent is incomplete, preserve plausible alternatives rather than inventing a rigid contract.",
            "source_refs": ["editorial-reference:application_guidance", "connordawg:application_guidance"],
        },
        {
            "id": "selects_before_fine_cuts",
            "guidance": "Identify meaningful scenes and their contributions before choosing exact cuts: orientation, a question, discovery, decision, changing stakes, performance, reaction, mood, consequence, or payoff. A passage may earn its place through experience or personality without introducing a new factual event. Local activity alone does not make every second necessary.",
            "source_refs": ["editorial-reference:application_guidance", "connordawg:gameplay_editing_rules"],
        },
        {
            "id": "assembly_and_dependencies",
            "guidance": "Consider the resulting sequence: what the viewer understands, expects, and feels before and after each selection. Preserve enough setup and consequence for important moments to work. Prefer a stronger representative scene when several serve the same purpose; do not require every scene to repeat its own full explanation.",
            "source_refs": ["connordawg:application_guidance", "editorial-reference:story_and_pacing_rules"],
        },
        {
            "id": "compare_repetition",
            "guidance": "Compare repeated activity as a set. Retain the examples that establish the situation, meaningful variation, adaptation, distinctive performance, setback, or supported result. Repetition can create tension, comedy, credibility, or a sense of effort; remove surplus repetitions when those contributions no longer change. Menus, traversal, dialogue, and quiet passages can each be meaningful or expendable depending on context.",
            "source_refs": ["editorial-reference:gameplay_editing_rules", "connordawg:gameplay_editing_rules"],
        },
        {
            "id": "rhythm_and_readability",
            "guidance": "Vary density rather than maximizing cuts. Give the viewer time to read consequential information, anticipate an event, feel sustained difficulty, or absorb a reaction. Compress routine stretches whose function is already clear. Runtime bounds express the creator's requested scope, not a quota of removals or a reason to pad the result.",
            "source_refs": ["editorial-reference:story_and_pacing_rules", "editorial-reference:application_guidance", "connordawg:story_and_pacing_rules"],
        },
        {
            "id": "live_performance",
            "guidance": "Treat live speech and vocal reactions as part of a performance, not merely a transcript. Keep valuable surprise, uncertainty, humor, frustration, timing, and the event that prompted them. Spoken words can be redundant; a quiet interval can carry discovery, suspense, execution, or atmosphere.",
            "source_refs": ["connordawg:narration_rules", "editorial-reference:style_selection_rules"],
        },
        {
            "id": "narration_for_actual_gaps",
            "guidance": "Choose the visual and live-audio account first, then identify what the proposed omissions leave unclear. Narration can supply missing causal context, accumulated learning, a transition, or reflection when the chosen format benefits from it. Prefer retained source speech or readable evidence when they already do the job. Plan where narration hands back to live sound; keep instructions for the editor separate from words or direction addressed to the narrator.",
            "source_refs": ["connordawg:application_guidance", "connordawg:narration_rules", "editorial-reference:narration_rules"],
        },
        {
            "id": "evidence_and_judgment",
            "guidance": "Keep observable facts, supplied context, uncertainty, and editorial preference distinct. Use the strongest relevant evidence for a factual claim; omission from a coarser summary is not a contradiction of a directly observed event. Preserve retrievable source references and decisive wording or state evidence so compressed records do not become the only available truth. A strong factual record establishes what happened, not whether it is enjoyable to watch.",
            "source_refs": ["editorial-reference:evidence_basis", "editorial-reference:uncertainties", "connordawg:application_guidance"],
        },
        {
            "id": "honest_transitions",
            "guidance": "Make material changes in time, place, session, attempt, resources, knowledge, or source provenance understandable when they matter to the account. Retrospective explanation may clarify later learning without making the player appear to know it earlier. Match outcome claims to supported evidence or clearly attributed context; an unresolved endpoint can still be a purposeful ending.",
            "source_refs": ["editorial-reference:gameplay_editing_rules", "connordawg:story_and_pacing_rules"],
        },
        {
            "id": "review_the_rough_sequence",
            "guidance": "Review the assembled selections and their joins, not only isolated cut justifications. Ask whether the sequence remains understandable, whether significant reactions and turning points survive, and whether repeated passages still earn their duration. Valid ranges and complete coverage establish technical correctness; pacing and entertainment value require separate assessment.",
            "source_refs": ["editorial-reference:application_guidance", "connordawg:application_guidance"],
        },
    ],
    "format_examples": [
        {
            "source_ref": "editorial-reference",
            "example": "The sampled AstralSpiff and LilAggy edits often use a designed opening followed by predominantly live commentary, preserving substantial discovery or challenge process. Opening-only narration and broad chronology describe those examples, not requirements for all projects.",
        },
        {
            "source_ref": "connordawg",
            "example": "The sampled Fear & Hunger retrospective repeatedly uses post-recorded narration to connect discontinuous selections, then returns to live reactions and readable game evidence. Its heavy compression and recurring narration are a format choice, not a universal target.",
        },
    ],
    "provenance": [
        {
            "id": "editorial-reference",
            "title": "Subtitler Editorial Reference Research",
            "artifact": "editorial-practices.json / result",
            "supporting_artifacts": [
                "artifacts/astralspiff-sequel-first-look-narration-open/finished-vod-comparison.json",
                "artifacts/lilaggy-challenge-narration-open/finished-vod-comparison.json",
            ],
            "limits": "Small creator-specific sample; two finished-to-VOD comparisons. Model-assisted analysis and curator notes, not a universal editing standard or a complete shot-by-shot reconstruction.",
        },
        {
            "id": "connordawg",
            "title": "Subtitler ConnorDawg Narration Research",
            "artifact": "narration-practices.json / result",
            "supporting_artifacts": [
                "artifacts/connordawg-fear-and-hunger-finished/finished-vod-comparison.json",
            ],
            "limits": "One finished-video/two-VOD comparison. Demonstrates a discovery-led retrospective; challenge-run prescriptions are editorial extrapolations. Transcript matching is incomplete.",
        },
    ],
}


def project_brief(project: dict[str, Any]) -> dict[str, Any]:
    """Copy explicit project intent without inventing audience or genre settings."""
    return copy.deepcopy({
        "title_or_game": project.get("title_or_game", ""),
        "objective": project.get("objective", ""),
        "must_keep_notes": project.get("must_keep_notes", []),
        "de_emphasize_notes": project.get("de_emphasize_notes", []),
        "target_duration_min_ms": project.get("target_duration_min_ms"),
        "target_duration_max_ms": project.get("target_duration_max_ms"),
        "output_locale": project.get("output_locale", "en"),
        "processing_locale": project.get("processing_locale", "en"),
    })
