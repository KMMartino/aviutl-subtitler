import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from subtitler.editorial_analysis import (
    EditorialAnalysisWindow,
    TranscriptEvidence,
    VisualEvidence,
    analyze_editorial_source,
    build_editorial_event_graph,
    build_activity_episode_layer,
    build_utterance_groups,
    build_editorial_analysis_windows,
    select_editorial_subtitles,
    synthesize_human_information_project,
)
from subtitler.errors import StructuredOutputIncompleteError


class _FakeProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.operations: list[str] = []

    def complete_structured(
        self,
        prompt: str,
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.prompts.append(prompt)
        self.operations.append(operation)
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
    def test_human_information_synthesis_emits_factual_multiscale_map(self) -> None:
        provider = _FakeProvider()
        provider.complete_structured = lambda prompt, **kwargs: (
            provider.prompts.append(prompt)
            or provider.operations.append(kwargs["operation"])
            or json.dumps({
                "progression_summary": "The player prepares, explores, and uses the key.",
                "event_phases": [{
                    "source_id": "source-1", "start_ms": 0, "end_ms": 10_000,
                    "label": "Opening stage", "summary": "The rules are learned.",
                    "category": "stage", "thread_ids": ["key"],
                }],
                "story_threads": [{
                    "title": "Key payoff", "summary": "An early key opens the exit.",
                    "category": "setup_payoff", "anchors": [{
                        "source_id": "source-1", "start_ms": 1000, "end_ms": 2000,
                        "label": "Key found", "relationship": "setup",
                    }, {
                        "source_id": "source-1", "start_ms": 8000, "end_ms": 9000,
                        "label": "Exit opened", "relationship": "payoff",
                    }],
                }],
                "narration_briefs": [{
                    "source_id": "source-1", "start_ms": 0, "end_ms": 2000,
                    "kind": "setup", "purpose": "Explain the unfamiliar premise.",
                    "memory_jog": "Introduce only the rules needed for the opening.",
                    "talking_points": ["Core loop"], "representative_visuals": ["Title screen"],
                    "thread_ids": [],
                }],
                "uncertainties": [],
            })
        )
        project = {
            "title_or_game": "Game", "objective": "First look", "output_locale": "en",
            "sources": [{
                "source_id": "source-1", "order": 0, "duration_ms": 10_000,
                "stages": {"semantic_spans": {"output": {"windows": []}}},
                "result": {"semantic_spans": [], "activity_episodes": [], "event_graph": {}},
            }],
        }

        result = synthesize_human_information_project(provider=provider, project=project)

        self.assertEqual(provider.operations, ["editorial_human_information"])
        self.assertEqual(result["workflow"], "human_information")
        self.assertEqual(len(result["event_phases"]), 1)
        self.assertEqual(len(result["global_threads"]), 1)
        self.assertEqual(len(result["narration_briefs"]), 1)
        self.assertIn("Produce no cut", provider.prompts[0])

    def test_activity_episode_reconciliation_joins_a_cross_window_activity(self) -> None:
        class EpisodeProvider:
            def complete_structured(
                self, prompt, *, max_tokens, operation, response_schema=None
            ):
                if operation == "editorial_episode_reconcile":
                    return json.dumps(
                        {
                            "episodes": [
                                {
                                    "episode_key": "character-creation",
                                    "level": 1,
                                    "parent_episode_key": "",
                                    "episode_kind": "setup",
                                    "start_ms": 200_000,
                                    "end_ms": 960_000,
                                    "label": "Character creation",
                                    "summary": "Build one character across several menus.",
                                    "continuity_key": "character-creation",
                                    "confidence": 0.96,
                                }
                            ]
                        }
                    )
                start_ms, end_ms = (
                    (200_000, 700_000)
                    if "Core processing range: 0-600000 ms" in prompt
                    else (500_000, 960_000)
                )
                return json.dumps(
                    {
                        "episodes": [
                            {
                                "episode_key": "local-character-creation",
                                "level": 1,
                                "parent_episode_key": "",
                                "episode_kind": "setup",
                                "start_ms": start_ms,
                                "end_ms": end_ms,
                                "label": "Character creation",
                                "summary": "Character setup continues.",
                                "continuity_key": "character-creation",
                                "confidence": 0.9,
                            }
                        ]
                    }
                )

        graph = {
            "nodes": [
                {
                    "event_id": "event-1",
                    "source_id": "source-1",
                    "start_ms": 200_000,
                    "end_ms": 600_000,
                    "visual_category": "character creator",
                },
                {
                    "event_id": "event-2",
                    "source_id": "source-1",
                    "start_ms": 600_000,
                    "end_ms": 960_000,
                    "visual_category": "equipment menu",
                },
            ],
            "edges": [],
        }
        episodes = build_activity_episode_layer(
            provider=EpisodeProvider(),
            source_id="source-1",
            source_duration_ms=1_000_000,
            title_or_game="Game",
            objective="Start the run",
            event_graph=graph,
            semantic_spans=[],
            analysis_windows=[
                EditorialAnalysisWindow(0, 0, 600_000, 0, 700_000),
                EditorialAnalysisWindow(1, 600_000, 1_000_000, 500_000, 1_000_000),
            ],
            output_locale="en",
            max_workers=1,
        )

        self.assertEqual(len(episodes), 1)
        self.assertEqual((episodes[0]["start_ms"], episodes[0]["end_ms"]), (200_000, 960_000))
        self.assertEqual(graph["nodes"][0]["activity_episode_ids"], [episodes[0]["episode_id"]])
        self.assertEqual(graph["nodes"][1]["activity_episode_ids"], [episodes[0]["episode_id"]])

    def test_alignment_fragments_form_edit_safe_utterance_groups(self) -> None:
        groups = build_utterance_groups(
            [
                TranscriptEvidence(0, 1_000, "This is"),
                TranscriptEvidence(1_100, 2_000, "one thought."),
                TranscriptEvidence(2_500, 3_000, "Next thought."),
                TranscriptEvidence(5_000, 6_000, "Separate thought"),
            ]
        )
        self.assertEqual(
            [(item["start_ms"], item["end_ms"], item["text"]) for item in groups],
            [
                (0, 2_000, "This is one thought."),
                (2_500, 3_000, "Next thought."),
                (5_000, 6_000, "Separate thought"),
            ],
        )

    def test_paused_continuation_clause_remains_one_edit_safe_thought(self) -> None:
        groups = build_utterance_groups(
            [
                TranscriptEvidence(0, 2_000, "ヒールを覚えたんだったら、"),
                TranscriptEvidence(11_000, 15_000, "主人公を器用貧乏にする必要はない。"),
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["start_ms"], groups[0]["end_ms"]), (0, 15_000))
        self.assertEqual(
            groups[0]["speech_spans"],
            [
                {"start_ms": 0, "end_ms": 2_000},
                {"start_ms": 11_000, "end_ms": 15_000},
            ],
        )

    def test_sentence_terminal_starts_a_new_meaning_unit_without_waiting_for_a_gap(self) -> None:
        groups = build_utterance_groups(
            [
                TranscriptEvidence(0, 2_000, "最初の考えです。"),
                TranscriptEvidence(2_050, 4_000, "次の考えです。"),
            ]
        )

        self.assertEqual(len(groups), 2)

    def test_continuous_long_speech_is_not_split_by_an_arbitrary_duration_cap(self) -> None:
        groups = build_utterance_groups(
            [
                TranscriptEvidence(0, 10_000, "This uninterrupted explanation"),
                TranscriptEvidence(10_100, 20_000, "continues across subtitle fragments"),
                TranscriptEvidence(20_100, 30_000, "and ends here."),
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["start_ms"], groups[0]["end_ms"]), (0, 30_000))

    def test_event_graph_marks_a_short_state_between_a_return_as_interruption(self) -> None:
        graph = build_editorial_event_graph(
            source_id="source-1",
            source_duration_ms=30_000,
            visuals=[
                VisualEvidence(0, 10_000, "Character creator", ("creator",), 0.9, None, "menu", "Creating character"),
                VisualEvidence(10_000, 15_000, "Difficulty menu", ("difficulty",), 0.9, None, "menu", "Selecting difficulty"),
                VisualEvidence(15_000, 30_000, "Character creator", ("creator",), 0.9, None, "menu", "Creating character"),
            ],
            semantic_spans=[
                {"start_ms": 0, "end_ms": 30_000, "label": "Setup", "summary": "Configure run"}
            ],
            audio_intents=[
                {"start_ms": 0, "end_ms": 30_000, "intent": "visual_review", "summary": ""}
            ],
        )

        self.assertEqual(len(graph["nodes"]), 3)
        self.assertTrue(graph["nodes"][1]["possible_interruption"])
        self.assertIn("returns_to", {edge["relationship"] for edge in graph["edges"]})
        self.assertEqual(
            [item["observed_label"] for item in graph["nodes"]],
            ["Creating character", "Selecting difficulty", "Creating character"],
        )
        self.assertTrue(
            all("semantic_label" not in item and "audio_intent" not in item for item in graph["nodes"])
        )


    def test_processing_windows_overlap_but_core_edge_avoids_active_speech(self) -> None:
        windows = build_editorial_analysis_windows(
            40 * 60 * 1000,
            transcript=[TranscriptEvidence(14 * 60 * 1000 + 50_000, 15 * 60 * 1000 + 10_000, "continuous speech")],
        )

        self.assertNotEqual(windows[0].end_ms, 15 * 60 * 1000)
        self.assertFalse(14 * 60 * 1000 + 50_000 < windows[0].end_ms < 15 * 60 * 1000 + 10_000)
        self.assertEqual(windows[1].evidence_start_ms, windows[1].start_ms - 90_000)



    def test_source_analysis_merges_independent_overlapping_windows(self) -> None:
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

        self.assertEqual(len(provider.prompts), 4)
        self.assertEqual(provider.operations.count("editorial_episode_map"), 2)
        editorial_prompts = [
            prompt for prompt, operation in zip(provider.prompts, provider.operations)
            if operation == "editorial_map"
        ]
        self.assertEqual(len(editorial_prompts), 2)
        self.assertIn("Initial objective", editorial_prompts[0])
        self.assertIn("Initial objective", editorial_prompts[1])
        self.assertIn("Silence is not an event classification", editorial_prompts[0])
        self.assertIn("Do not recommend cuts", editorial_prompts[0])
        self.assertEqual(
            (result["semantic_spans"][0]["start_ms"], result["semantic_spans"][0]["end_ms"]),
            (0, 90_000),
        )
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["creative_suggestions"], [])
        self.assertIn("editorial_map", provider.operations)
        self.assertIsNotNone(provider.response_schema)
        self.assertTrue(any("mapping event window 1/2" in message for message in progress))
        self.assertTrue(any("event window 2/2 complete" in message for message in progress))

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

    def test_model_ranges_are_normalized_to_safe_factual_spans(self) -> None:
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
                        "semantic_spans": [{
                            "start_ms": -1,
                            "end_ms": 999_999,
                            "label": "Out-of-range event",
                            "kind": "activity",
                            "summary": "Observed activity",
                            "confidence": 4,
                            "evidence_refs": [],
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
        span = result["semantic_spans"][0]
        self.assertEqual((span["start_ms"], span["end_ms"]), (0, 10_000))
        self.assertEqual(span["label"], "Out-of-range event")
        self.assertEqual(span["confidence"], 1.0)



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
                if operation == "editorial_map" and self.attempts == 2:
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
            max_workers=1,
        )

        self.assertEqual(provider.attempts, 6)
        self.assertEqual(provider.operations.count("editorial_map"), 3)
        self.assertEqual(len(result["windows"]), 3)
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








    def test_selective_subtitles_use_raw_transcript_and_respect_final_plan(self) -> None:
        class SubtitleProvider:
            def __init__(self) -> None:
                self.prompt = ""

            def complete_structured(self, prompt, *, max_tokens, operation, response_schema=None):
                self.prompt = prompt
                self.operation = operation
                self.response_schema = response_schema
                return json.dumps({
                    "selected_phrases": [{
                        "start_ms": 1_000,
                        "end_ms": 2_000,
                        "exact_phrase": "This changes everything",
                        "reason": "Consequential realization",
                        "emphasis_energy": 0.7,
                        "confidence": 0.9,
                    }]
                })

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            timing_path = root / "transcript.subtitle_timing.csv"
            text_path = root / "transcript.final_text.txt"
            timing_path.write_text("start,end\n1.0,2.0\n", encoding="utf-8")
            text_path.write_text("1. This changes everything\n", encoding="utf-8")
            project = {
                "title_or_game": "Game",
                "objective": "First playthrough",
                "output_locale": "en",
                "sources": [{
                    "source_id": "source-1",
                    "order": 0,
                    "duration_ms": 10_000,
                    "result": {
                        "safe_boundaries_ms": [0, 10_000],
                        "semantic_spans": [],
                        "event_graph": {"nodes": []},
                    },
                    "stages": {"transcription": {"output": {
                        "timing_path": str(timing_path),
                        "text_path": str(text_path),
                    }}},
                }],
            }
            provider = SubtitleProvider()
            selected = select_editorial_subtitles(
                provider=provider,
                project=project,
                final_actions=[{
                    "source_id": "source-1",
                    "start_ms": 0,
                    "end_ms": 10_000,
                    "action_type": "preserve",
                    "operation_ranges": [],
                }],
                story_actions=[],
                max_workers=1,
            )

        self.assertEqual(provider.operation, "editorial_selective_subtitles")
        self.assertIn("This changes everything", provider.prompt)
        self.assertEqual(selected[0]["source_text"], "This changes everything")

        cut_selected = select_editorial_subtitles(
            provider=provider,
            project=project,
            final_actions=[{
                "source_id": "source-1",
                "start_ms": 0,
                "end_ms": 10_000,
                "action_type": "cut",
                "operation_ranges": [],
            }],
            story_actions=[],
            max_workers=1,
        )
        self.assertEqual(cut_selected, [])


if __name__ == "__main__":
    unittest.main()
