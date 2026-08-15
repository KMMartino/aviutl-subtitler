import json
import unittest
from typing import Any

from subtitler.editorial_analysis import (
    EDITORIAL_OUTPUT_MAX_TOKENS,
    TranscriptEvidence,
    VisualEvidence,
    analyze_editorial_source,
    build_timeline_coverage,
    deduplicate_creative_suggestions,
    reconcile_editorial_project,
    review_editorial_project,
)
from subtitler.errors import StructuredOutputIncompleteError


class _FakeProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_structured(
        self,
        prompt: str,
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.prompts.append(prompt)
        self.max_tokens = max_tokens
        self.operation = operation
        self.response_schema = response_schema
        return json.dumps(
            {
                "summary": "An objective continues.",
                "context_update": {"open_threads": ["Find the exit"]},
                "semantic_spans": [{"start_ms": -500, "end_ms": 90_000, "label": "Traversal"}],
                "recommendations": [
                    {
                        "start_ms": 1_000,
                        "end_ms": 20_000,
                        "disposition": "condense",
                        "presentation_mode": "live_excerpt",
                        "reason": "Repeated route",
                        "continuity_case": "Establishes geography",
                        "subtraction_case": "The route has already been shown",
                        "selection_case": "Keep the reaction at the end",
                        "context_dependencies": ["Preserve arrival"],
                        "confidence": 0.8,
                    }
                ],
                "narration_briefs": [],
                "creative_suggestions": [{
                    "start_ms": 2_000,
                    "end_ms": 4_000,
                    "type": "punch_in",
                    "suggestion": "Punch in on the reaction.",
                    "backup_option": "Use emphasis text only.",
                    "trigger": "The creator reacts to the hit",
                    "confidence": 0.85,
                }],
                "connections": [],
            },
            ensure_ascii=False,
        )


class EditorialAnalysisTests(unittest.TestCase):
    def test_timeline_coverage_makes_unmarked_ranges_explicit(self) -> None:
        coverage = build_timeline_coverage(
            10_000,
            [{"id": "rec-1", "start_ms": 2_000, "end_ms": 4_000}],
        )
        self.assertEqual(
            [(item["start_ms"], item["end_ms"], item["status"]) for item in coverage],
            [
                (0, 2_000, "leave_as_is"),
                (2_000, 4_000, "suggested"),
                (4_000, 10_000, "leave_as_is"),
            ],
        )

    def test_nearby_duplicate_creative_suggestions_keep_stronger_item(self) -> None:
        values = deduplicate_creative_suggestions(
            [
                {"id": "weak", "source_id": "s", "type": "punch_in", "start_ms": 1_000, "end_ms": 2_000, "suggestion": "Punch in on reaction", "confidence": 0.7},
                {"id": "strong", "source_id": "s", "type": "punch_in", "start_ms": 1_500, "end_ms": 2_500, "suggestion": "Punch in on the reaction", "confidence": 0.9},
            ]
        )
        self.assertEqual([item["id"] for item in values], ["strong"])

    def test_source_analysis_carries_context_across_ordinary_windows(self) -> None:
        provider = _FakeProvider()
        progress: list[str] = []
        result = analyze_editorial_source(
            provider=provider,
            source_id="source-1",
            source_duration_ms=120_000,
            title_or_game="Untitled recording",
            objective="Complete the session",
            transcript=[TranscriptEvidence(1_000, 2_000, "I am concentrating now")],
            visuals=[VisualEvidence(0, 60_000, "Continuous encounter")],
            cumulative_context={"open_threads": ["Initial objective"]},
            window_ms=60_000,
            progress=progress.append,
        )

        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("Initial objective", provider.prompts[0])
        self.assertIn("Find the exit", provider.prompts[1])
        self.assertIn("Silence is neutral", provider.prompts[0])
        self.assertIn("Continuity-first", provider.prompts[0])
        self.assertIn("Selection-first", provider.prompts[0])
        self.assertEqual(result["recommendations"][0]["continuity_case"], "Establishes geography")
        self.assertEqual(result["semantic_spans"][0]["start_ms"], 0)
        self.assertEqual(result["semantic_spans"][1]["start_ms"], 60_000)
        self.assertEqual(result["creative_suggestions"][0]["type"], "punch_in")
        self.assertEqual(result["creative_suggestions"][0]["source_id"], "source-1")
        self.assertEqual(provider.operation, "editorial_map")
        self.assertEqual(provider.max_tokens, EDITORIAL_OUTPUT_MAX_TOKENS)
        self.assertIsNotNone(provider.response_schema)
        self.assertIn("mapping window 1/2", progress[0])
        self.assertIn("window 2/2 complete", progress[-1])

    def test_source_analysis_requests_japanese_human_facing_output(self) -> None:
        provider = _FakeProvider()
        analyze_editorial_source(
            provider=provider,
            source_id="source-1",
            source_duration_ms=10_000,
            title_or_game="Game",
            objective="Explain the run",
            transcript=[],
            visuals=[],
            output_locale="ja",
        )

        self.assertIn(
            "Write every human-facing free-text JSON value in natural, concise Japanese.",
            provider.prompts[0],
        )
        self.assertIn("Do not translate verbatim transcript excerpts.", provider.prompts[0])

    def test_model_ranges_and_enums_are_normalized_to_safe_suggestions(self) -> None:
        class UnsafeProvider(_FakeProvider):
            def complete_structured(
                self,
                prompt: str,
                *,
                max_tokens: int,
                operation: str,
                response_schema: dict[str, Any] | None = None,
            ) -> str:
                return json.dumps(
                    {
                        "recommendations": [{
                            "start_ms": -1,
                            "end_ms": 999_999,
                            "disposition": "delete_now",
                            "presentation_mode": "replace_source",
                            "confidence": 4,
                        }]
                    }
                )

        result = analyze_editorial_source(
            provider=UnsafeProvider(),
            source_id="source-1",
            source_duration_ms=10_000,
            title_or_game="Recording",
            objective="Summarize it",
            transcript=[],
            visuals=[],
        )
        suggestion = result["recommendations"][0]
        self.assertEqual((suggestion["start_ms"], suggestion["end_ms"]), (0, 10_000))
        self.assertEqual(suggestion["disposition"], "review")
        self.assertEqual(suggestion["presentation_mode"], "live_excerpt")
        self.assertEqual(suggestion["confidence"], 1.0)

    def test_global_reconciliation_selects_one_plan_within_duration_range(self) -> None:
        class GlobalProvider(_FakeProvider):
            def complete_structured(
                self,
                prompt: str,
                *,
                max_tokens: int,
                operation: str,
                response_schema: dict[str, Any] | None = None,
            ) -> str:
                self.prompts.append(prompt)
                return json.dumps({
                    "duration_budget": {
                        "source_total_ms": 10_000,
                        "target_min_ms": 3_000,
                        "target_max_ms": 5_000,
                        "estimated_final_ms": 4_200,
                        "within_target_range": True,
                        "warning": "",
                    },
                    "editorial_direction_summary": "Preserve the encounter and trim repeated setup.",
                    "optimal_plan": [
                        {"recommendation_id": "invented", "priority": 0, "reason": "Invalid", "selected_kept_ms": 0},
                        {"recommendation_id": "rec-1", "priority": 1, "reason": "Representative excerpt", "selected_kept_ms": 2_500},
                    ],
                })

        provider = GlobalProvider()
        result = reconcile_editorial_project(provider=provider, project={
            "title_or_game": "Recording",
            "objective": "Find the story",
            "target_duration_min_ms": 3_000,
            "target_duration_max_ms": 5_000,
            "must_keep_notes": [],
            "de_emphasize_notes": [],
            "sources": [{"source_id": "source-1", "order": 0, "duration_ms": 10_000, "result": {}, "stages": {}}],
            "editorial_map": {"recommendations": [{
                "id": "rec-1", "source_id": "source-1", "start_ms": 0, "end_ms": 4_000,
                "estimated_kept_min_ms": 1_000, "estimated_kept_max_ms": 2_000,
            }]},
        })

        self.assertIn("Requested lower final duration ms: 3000", provider.prompts[0])
        self.assertIn("Preferred midpoint final duration ms: 4000", provider.prompts[0])
        self.assertIn("exactly one editorial plan", provider.prompts[0])
        self.assertEqual(result["duration_budget"]["target_max_ms"], 5_000)
        self.assertEqual([item["recommendation_id"] for item in result["optimal_plan"]], ["rec-1"])
        self.assertEqual(result["optimal_plan"][0]["selected_kept_ms"], 2_000)

    def test_global_reconciliation_respects_checkpoint_output_locale(self) -> None:
        class GlobalProvider(_FakeProvider):
            def complete_structured(
                self,
                prompt: str,
                *,
                max_tokens: int,
                operation: str,
                response_schema: dict[str, Any] | None = None,
            ) -> str:
                self.prompts.append(prompt)
                return json.dumps({})

        provider = GlobalProvider()
        reconcile_editorial_project(
            provider=provider,
            project={
                "output_locale": "ja",
                "title_or_game": "Game",
                "objective": "Explain the run",
                "target_duration_min_ms": 3_000,
                "target_duration_max_ms": 5_000,
                "must_keep_notes": [],
                "de_emphasize_notes": [],
                "sources": [],
                "editorial_map": {"recommendations": []},
            },
        )

        self.assertIn("natural, concise Japanese", provider.prompts[0])

    def test_output_limited_window_is_split_and_only_failed_range_is_retried(self) -> None:
        class OverflowProvider(_FakeProvider):
            def __init__(self) -> None:
                super().__init__()
                self.attempts = 0

            def complete_structured(
                self,
                prompt: str,
                *,
                max_tokens: int,
                operation: str,
                response_schema: dict[str, Any] | None = None,
            ) -> str:
                self.attempts += 1
                if self.attempts == 1:
                    raise StructuredOutputIncompleteError(
                        "output limited", reason="max_output_tokens"
                    )
                return super().complete_structured(
                    prompt,
                    max_tokens=max_tokens,
                    operation=operation,
                    response_schema=response_schema,
                )

        provider = OverflowProvider()
        progress: list[str] = []
        result = analyze_editorial_source(
            provider=provider,
            source_id="source-1",
            source_duration_ms=30 * 60 * 1000,
            title_or_game="Recording",
            objective="Find the story",
            transcript=[
                TranscriptEvidence(1_000, 2_000, "Start"),
                TranscriptEvidence(15 * 60 * 1000, 15 * 60 * 1000 + 2_000, "Middle"),
            ],
            visuals=[],
            progress=progress.append,
        )

        self.assertEqual(provider.attempts, 3)
        self.assertEqual(len(result["windows"]), 2)
        self.assertTrue(any("output limit reached" in message for message in progress))

    def test_completed_base_window_can_be_reused_without_another_request(self) -> None:
        first = _FakeProvider()
        records: list[dict[str, Any]] = []
        initial = analyze_editorial_source(
            provider=first,
            source_id="source-1",
            source_duration_ms=60_000,
            title_or_game="Recording",
            objective="Find the story",
            transcript=[],
            visuals=[],
            window_completed=records.append,
        )
        second = _FakeProvider()
        resumed = analyze_editorial_source(
            provider=second,
            source_id="source-1",
            source_duration_ms=60_000,
            title_or_game="Recording",
            objective="Find the story",
            transcript=[],
            visuals=[],
            completed_windows=records,
        )

        self.assertEqual(second.prompts, [])
        self.assertEqual(resumed["recommendations"], initial["recommendations"])

    def test_final_director_reviews_global_experience_and_filters_unknown_ids(self) -> None:
        class DirectorProvider(_FakeProvider):
            def complete_structured(
                self,
                prompt: str,
                *,
                max_tokens: int,
                operation: str,
                response_schema: dict[str, Any] | None = None,
            ) -> str:
                self.prompts.append(prompt)
                self.operation = operation
                self.response_schema = response_schema
                return json.dumps({
                    "executive_direction": "Protect the payoff.",
                    "pacing_assessment": "Compress one repeated setup.",
                    "intrigue_assessment": "The objective remains legible.",
                    "information_density_assessment": "Narration is sparse.",
                    "continuity_assessment": "Causality is intact.",
                    "priority_changes": [
                        {"recommendation_id": "rec-1", "priority": 1, "action": "Shorten", "rationale": "Repeated"},
                        {"recommendation_id": "invented", "priority": 2, "action": "Cut", "rationale": "Unsupported"},
                    ],
                    "protected_moments": [
                        {"recommendation_id": "rec-1", "rationale": "Contains the payoff"}
                    ],
                    "unresolved_questions": ["Is the callback visually clear?"],
                    "estimated_final_ms": 4_000,
                    "final_actions": [{
                        "action_id": "draft-a",
                        "action_type": "preserve",
                        "source_id": "source-1",
                        "start_ms": 0,
                        "end_ms": 4_000,
                        "instruction": "Keep the successful attempt intact.",
                        "rationale": "The live reaction carries the payoff.",
                        "priority": 1,
                        "confidence": 0.9,
                        "recommendation_ids": ["rec-1"],
                        "narration_brief_ids": ["nar-1"],
                        "supporting_edit_ids": ["draft-edit"],
                        "thread_ids": [],
                        "operation_ranges": [],
                    }, {
                        "action_id": "draft-invalid",
                        "action_type": "trim",
                        "source_id": "source-1",
                        "start_ms": 8_000,
                        "end_ms": 7_000,
                        "instruction": "Invalid reversed range.",
                        "rationale": "Should be rejected.",
                        "priority": 2,
                        "confidence": 0.4,
                        "recommendation_ids": [],
                        "narration_brief_ids": [],
                        "supporting_edit_ids": [],
                        "thread_ids": [],
                        "operation_ranges": [],
                    }],
                    "supporting_edits": [{
                        "edit_id": "draft-edit",
                        "parent_action_id": "draft-a",
                        "action_type": "punch_in",
                        "source_id": "source-1",
                        "start_ms": 2_000,
                        "end_ms": 2_500,
                        "instruction": "Punch in on the reaction.",
                        "rationale": "The expression lands the payoff.",
                        "confidence": 0.8,
                        "thread_ids": [],
                        "evidence_request": False,
                        "reference_query": "",
                        "reference_source_ids": [],
                    }],
                    "threads": [],
                })

        provider = DirectorProvider()
        project = {
            "title_or_game": "Recording",
            "objective": "Find the story",
            "target_duration_min_ms": 3_000,
            "target_duration_max_ms": 5_000,
            "sources": [{
                "source_id": "source-1",
                "order": 0,
                "duration_ms": 10_000,
                "stages": {"semantic_spans": {"output": {"windows": [{"summary": "Setup and payoff"}]}}},
            }],
            "editorial_map": {
                "recommendations": [{"id": "rec-1", "source_id": "source-1", "start_ms": 0, "end_ms": 4_000}],
                "narration_briefs": [{"id": "nar-1", "source_id": "source-1"}],
                "creative_suggestions": [],
            },
        }

        result = review_editorial_project(
            provider=provider,
            project=project,
            reconciliation={
                "editorial_direction_summary": "Keep the payoff",
                "optimal_plan": [{"recommendation_id": "rec-1"}],
            },
        )

        self.assertEqual(provider.operation, "editorial_director")
        self.assertIsNotNone(provider.response_schema)
        self.assertIn("pacing across the complete arc", provider.prompts[0])
        self.assertIn("Never emit shorter/longer alternatives", provider.prompts[0])
        self.assertEqual(
            [item["recommendation_id"] for item in result["priority_changes"]],
            ["rec-1"],
        )
        self.assertEqual(result["protected_moments"][0]["recommendation_id"], "rec-1")
        self.assertEqual(result["final_actions"][0]["action_type"], "preserve")
        self.assertEqual(len(result["final_actions"]), 1)
        self.assertEqual(result["final_actions"][0]["narration_brief_ids"], [])
        self.assertEqual(result["supporting_edits"][0]["parent_action_id"], "action-001")

    def test_final_director_resolves_overlaps_and_fills_gaps_with_preserve(self) -> None:
        class CoverageProvider(_FakeProvider):
            def complete_structured(
                self,
                prompt: str,
                *,
                max_tokens: int,
                operation: str,
                response_schema: dict[str, Any] | None = None,
            ) -> str:
                self.prompts.append(prompt)
                return json.dumps({
                    "final_actions": [{
                        "action_id": "trim",
                        "action_type": "trim",
                        "source_id": "source-1",
                        "start_ms": 1_000,
                        "end_ms": 4_000,
                        "instruction": "Remove the repeated route.",
                        "rationale": "The destination is already established.",
                        "priority": 1,
                        "confidence": 0.9,
                    }, {
                        "action_id": "keep",
                        "action_type": "preserve",
                        "source_id": "source-1",
                        "start_ms": 3_000,
                        "end_ms": 6_000,
                        "instruction": "Keep the live reaction.",
                        "rationale": "The reaction carries the moment.",
                        "priority": 2,
                        "confidence": 0.8,
                    }],
                })

        provider = CoverageProvider()
        result = review_editorial_project(
            provider=provider,
            project={
                "title_or_game": "Recording",
                "objective": "Find the story",
                "target_duration_min_ms": 5_000,
                "target_duration_max_ms": 9_000,
                "output_locale": "en",
                "sources": [{
                    "source_id": "source-1",
                    "order": 0,
                    "duration_ms": 10_000,
                    "stages": {},
                }],
                "editorial_map": {
                    "recommendations": [],
                    "narration_briefs": [],
                    "creative_suggestions": [],
                },
            },
            reconciliation={},
        )

        self.assertEqual(
            [
                (item["action_type"], item["start_ms"], item["end_ms"])
                for item in result["final_actions"]
            ],
            [
                ("preserve", 0, 1_000),
                ("trim", 1_000, 4_000),
                ("preserve", 4_000, 10_000),
            ],
        )
        self.assertIn("must not overlap", provider.prompts[0])


if __name__ == "__main__":
    unittest.main()
