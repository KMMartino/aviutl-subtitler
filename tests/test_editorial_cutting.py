import tempfile
import unittest
from pathlib import Path

from subtitler.editorial_cutting import build_human_information_plan
from subtitler.editorial_exo import _cutting_assistant_marker_layers, _utterance_reference_markers
from subtitler.editorial_project import EditorialProjectOptions, EditorialSourceInput, create_editorial_project
from subtitler.editorial_report import render_editorial_html


class CuttingAssistantTests(unittest.TestCase):
    def test_human_information_plan_marks_only_voice_free_gaps(self) -> None:
        project = {"sources": [{
            "source_id": "source-1", "duration_ms": 9000,
            "result": {"speech_segments": [
                {"start_ms": 3000, "end_ms": 3500},
                {"start_ms": 6000, "end_ms": 6500},
            ]},
        }]}
        synthesis = {
            "event_phases": [], "global_threads": [],
            "narration_briefs": [{
                "source_id": "source-1", "start_ms": 0, "end_ms": 1900,
                "kind": "setup", "purpose": "Introduce the run.",
                "memory_jog": "State the premise.", "talking_points": ["Premise"],
                "representative_visuals": ["Title screen"], "thread_ids": [],
            }],
        }

        result = build_human_information_plan(project=project, synthesis=synthesis)

        self.assertEqual(result["workflow"], "human_information")
        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in result["confirmed_cuts"]],
            [(0, 2950), (3600, 5950), (6600, 9000)],
        )
        self.assertTrue(all(item["candidate_kind"] == "voice_free_gap" for item in result["confirmed_cuts"]))
        self.assertEqual(len(result["final_actions"]), 1)

    def test_human_information_plan_ignores_gap_under_two_seconds(self) -> None:
        project = {"sources": [{
            "source_id": "source-1", "duration_ms": 5000,
            "result": {"speech_segments": [
                {"start_ms": 0, "end_ms": 1000},
                {"start_ms": 2700, "end_ms": 5000},
            ]},
        }]}

        result = build_human_information_plan(
            project=project,
            synthesis={"event_phases": [], "global_threads": [], "narration_briefs": []},
        )

        self.assertEqual(result["confirmed_cuts"], [])

    def test_human_information_plan_falls_back_to_utterances(self) -> None:
        project = {"sources": [{
            "source_id": "source-1", "duration_ms": 6000,
            "result": {"utterance_groups": [{
                "start_ms": 2000, "end_ms": 3000, "text": "A thought",
            }]},
        }]}

        result = build_human_information_plan(
            project=project,
            synthesis={"event_phases": [], "global_threads": [], "narration_briefs": []},
        )

        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in result["confirmed_cuts"]],
            [(3100, 6000)],
        )

    def test_flat_presentation_contains_only_cut_map_and_narration_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "game.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 20_000, frame_rate=60)],
                EditorialProjectOptions("Game", "First playthrough", 10_000, 18_000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"].update({
                "workflow": "human_information", "status": "complete",
                "confirmed_cuts": [{
                    "source_id": source_id, "start_ms": 4000, "end_ms": 7000,
                    "candidate_kind": "voice_free_gap",
                }],
                "removed_ms": 3000,
                "final_actions": [{
                    "action_id": "narration-001", "action_type": "narrated_summary",
                    "source_id": source_id, "start_ms": 0, "end_ms": 3000,
                    "instruction": "Introduce the run.",
                    "narration_guidance": {
                        "purpose": "Introduce the run", "vision": "One concise opening",
                        "talking_points": ["Premise"], "representative_visuals": ["Title screen"],
                    },
                }],
            })

            rendered = render_editorial_html(artifact)
            layers = _cutting_assistant_marker_layers(artifact, {source_id: 0.0})

        self.assertIn("Cut-marker workflow", rendered)
        self.assertIn("Narration possibilities", rendered)
        self.assertNotIn("Why", rendered)
        self.assertEqual(
            [[marker.text for marker in layer] for layer in layers],
            [["[CUT] Cut"], ["NARRATION"]],
        )

    def test_every_utterance_becomes_one_exact_reference_span(self) -> None:
        artifact = {"output_locale": "ja", "sources": [{
            "source_id": "source-1", "order": 0,
            "result": {"utterance_groups": [
                {"start_ms": 1000, "end_ms": 2500, "text": "最初の発話"},
                {"start_ms": 4000, "end_ms": 7000, "text": "次の発話"},
            ]},
        }]}

        markers = _utterance_reference_markers(artifact, {"source-1": 10.0})

        self.assertEqual(
            [(item.start_time, item.end_time, item.text) for item in markers],
            [(11.0, 12.5, "発話 0001: 最初の発話"), (14.0, 17.0, "発話 0002: 次の発話")],
        )

    def test_utterance_guides_fill_handles_next_to_generated_cuts(self) -> None:
        artifact = {
            "output_locale": "en",
            "editorial_map": {"workflow": "human_information", "confirmed_cuts": [
                {"source_id": "source-1", "start_ms": 0, "end_ms": 950},
                {"source_id": "source-1", "start_ms": 2600, "end_ms": 3950},
            ]},
            "sources": [{
                "source_id": "source-1", "order": 0,
                "result": {"utterance_groups": [
                    {"start_ms": 1000, "end_ms": 2500, "text": "First thought"},
                    {"start_ms": 4000, "end_ms": 5000, "text": "Second thought"},
                ]},
            }],
        }

        markers = _utterance_reference_markers(artifact, {"source-1": 0.0})

        self.assertEqual([(item.start_time, item.end_time) for item in markers], [(0.95, 2.6), (3.95, 5.0)])

    def test_long_utterance_reference_preserves_and_wraps_full_text(self) -> None:
        utterance_text = "長い発話内容" * 80
        artifact = {"output_locale": "ja", "sources": [{
            "source_id": "source-1", "order": 0,
            "result": {"utterance_groups": [{
                "start_ms": 1000, "end_ms": 9000, "text": utterance_text,
            }]},
        }]}

        markers = _utterance_reference_markers(artifact, {"source-1": 0.0})

        self.assertEqual(len(markers), 1)
        self.assertIn("\n", markers[0].text)
        self.assertTrue("".join(markers[0].text.split()).endswith(utterance_text))


if __name__ == "__main__":
    unittest.main()
