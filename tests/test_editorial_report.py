import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.editorial_project import EditorialProjectOptions, EditorialSourceInput, create_editorial_project
from subtitler.editorial_report import render_editorial_html, write_editorial_html


class EditorialReportTests(unittest.TestCase):
    def test_human_information_report_is_a_narration_and_story_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "game.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("Game", "First look", 20_000, 50_000),
            )
            source_id = project["sources"][0]["source_id"]
            project["editorial_map"].update({
                "workflow": "human_information",
                "progression_summary": "The player learns the rules and reaches the boss.",
                "confirmed_cuts": [{
                    "source_id": source_id, "start_ms": 1000, "end_ms": 2500,
                }],
                "final_actions": [{
                    "action_id": "narration-001", "action_type": "narrated_summary",
                    "source_id": source_id, "start_ms": 0, "end_ms": 5000,
                    "narration_guidance": {
                        "purpose": "Introduce the unfamiliar game.",
                        "vision": "Give one cohesive setup.",
                        "talking_points": ["Premise"], "representative_visuals": ["Title"],
                    },
                }],
                "event_phases": [{
                    "source_id": source_id, "start_ms": 0, "end_ms": 60_000,
                    "label": "Opening stage", "summary": "The rules are discovered.",
                }],
                "global_threads": [],
            })

            rendered = render_editorial_html(project)

        self.assertNotIn("Narration dashboard", rendered)
        self.assertIn("Narration possibilities", rendered)
        self.assertIn("Use the reviewed range to generate a factual narration brief.", rendered)
        self.assertNotIn("Give one cohesive setup", rendered)
        self.assertIn("Factual progression", rendered)
        self.assertIn("Voice-free review markers", rendered)
        self.assertNotIn("Cut map", rendered)
        self.assertNotIn("00:01–00:02", rendered)
        self.assertIn("Initial cut markers remain visible even underneath narration possibilities.", rendered)

    def test_operation_labels_follow_the_parent_editorial_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "game.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("Game", "Finish", 20_000, 40_000),
            )
            source_id = project["sources"][0]["source_id"]
            project["editorial_map"]["final_actions"] = [{
                "action_id": "action-001",
                "action_type": "montage",
                "source_id": source_id,
                "start_ms": 0,
                "end_ms": 60_000,
                "instruction": "Build a short montage from the marked clips.",
                "rationale": "The complete route is repetitive.",
                "operation_ranges": [{
                    "source_id": source_id,
                    "start_ms": 10_000,
                    "end_ms": 15_000,
                    "role": "keep",
                    "note": "Keep the discovery.",
                }],
                "narration_brief_ids": [],
                "supporting_edit_ids": [],
                "thread_ids": [],
            }]

            rendered = render_editorial_html(project)

            self.assertIn("MONTAGE · game-001-1", rendered)
            self.assertNotIn("SELECT HIGHLIGHTS · game-001-1", rendered)

    def test_final_plan_uses_explicit_links_numbered_directions_and_thread_colors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "game.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 120_000)],
                EditorialProjectOptions("Game", "Finish", 60_000, 90_000),
            )
            source_id = project["sources"][0]["source_id"]
            project["editorial_map"]["editorial_direction_summary"] = "Protect the live payoff."
            project["editorial_map"]["final_actions"] = [{
                "action_id": "action-001",
                "action_type": "preserve",
                "source_id": source_id,
                "start_ms": 10_000,
                "end_ms": 20_000,
                "instruction": "Keep the successful attempt intact.",
                "rationale": "The live reaction is the payoff.",
                "narration_brief_ids": [],
                "supporting_edit_ids": ["edit-001"],
                "thread_ids": ["thread-001"],
            }]
            project["editorial_map"]["supporting_edits"] = [{
                "edit_id": "edit-001",
                "parent_action_id": "action-001",
                "action_type": "callback",
                "source_id": source_id,
                "start_ms": 18_000,
                "end_ms": 19_000,
                "instruction": "Briefly recall the failed opening attempt.",
                "rationale": "It makes the improvement legible.",
                "thread_ids": ["thread-001"],
            }]
            project["editorial_map"]["editorial_threads"] = [{
                "thread_id": "thread-001",
                "title": "Failure to payoff",
                "editorial_use": "Connect the opening failure to the win.",
            }]

            rendered = render_editorial_html(project)

            self.assertIn('href="#direction-1"', rendered)
            self.assertIn("1. game-001", rendered)
            self.assertEqual(rendered.count("Failure to payoff"), 2)
            self.assertIn("Keep the successful attempt intact.", rendered)
            self.assertIn("Briefly recall the failed opening attempt.", rendered)
            self.assertNotIn("Standalone narration", rendered)

    def test_japanese_report_localizes_report_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recording.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 120_000)],
                EditorialProjectOptions(
                    "ゲーム",
                    "挑戦を説明する",
                    60_000,
                    90_000,
                    output_locale="ja",
                ),
            )
            project["editorial_map"]["final_actions"] = [{
                "action_id": "action-001",
                "action_type": "preserve",
                "source_id": project["sources"][0]["source_id"],
                "start_ms": 0,
                "end_ms": 10_000,
                "instruction": "この場面を残す。",
                "rationale": "導入に必要。",
                "story_beat_number": 1,
                "narration_brief_ids": [],
                "supporting_edit_ids": [],
                "thread_ids": [],
            }]

            rendered = render_editorial_html(project)

            self.assertIn('<html lang="ja">', rendered)
            self.assertIn("目標時間", rendered)
            self.assertIn("編集提案", rendered)
            self.assertIn("展開 1", rendered)
            self.assertNotIn("構成 1", rendered)
            self.assertIn("最終プランはタイムライン全体を対象", rendered)
            self.assertNotIn("No editorial recommendations have been generated yet.", rendered)

    def test_report_shows_one_suggestion_and_backup_without_machine_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recording.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 7_200_000)],
                EditorialProjectOptions("Game <run>", "Finish & explain", 3_600_000, 5_400_000),
            )
            project["editorial_map"]["recommendations"] = [{
                "id": "recommendation-1",
                "source_id": project["sources"][0]["source_id"],
                "start_ms": 10_000,
                "end_ms": 20_000,
                "reason": "Repeated <route>",
                "viewer_benefit": "Faster progression",
                "disposition": "condense",
                "presentation_mode": "live_excerpt",
                "confidence": 0.75,
                "continuity_case": "Preserves geography",
                "subtraction_case": "Already established",
                "selection_case": "Keep the arrival",
            }, {
                "id": "recommendation-2",
                "source_id": project["sources"][0]["source_id"],
                "start_ms": 21_000,
                "end_ms": 25_000,
                "reason": "Unselected detour",
                "disposition": "condense",
                "presentation_mode": "live_excerpt",
                "confidence": 0.7,
            }]
            project["editorial_map"]["global_reconciliation"]["status"] = "complete"
            project["editorial_map"]["editorial_direction_summary"] = "Use one balanced plan."
            project["editorial_map"]["duration_budget"] = {
                "estimated_final_ms": 4_200_000,
                "warning": "",
            }
            project["editorial_map"]["optimal_plan"] = [{
                "recommendation_id": "recommendation-1",
                "priority": 1,
                "reason": "Best edit",
                "selected_kept_ms": 5_000,
            }]
            project["editorial_map"]["narration_briefs"] = [{
                "id": "narration-1",
                "source_id": project["sources"][0]["source_id"],
                "start_ms": 12_000,
                "end_ms": 18_000,
                "purpose": "Bridge the repeated route",
                "memory_jog": "Explain why the second traversal mattered.",
                "talking_points": ["The route was already established"],
            }]
            project["editorial_map"]["creative_suggestions"] = [{
                "id": "creative-standalone",
                "source_id": project["sources"][0]["source_id"],
                "start_ms": 30_000,
                "end_ms": 31_000,
                "type": "punch_in",
                "suggestion": "Punch in on the reaction.",
                "backup_option": "Leave it clean.",
            }]
            project["editorial_map"]["director_review"] = {
                "style_contract": {
                    "opening_mode": "opening_narration",
                    "viewer_promise": "Understand the premise, then experience the live discovery.",
                    "narration_policy": "Use narration for the opening setup only.",
                    "summary": "Preserve discovery and compress repeated travel.",
                },
                "executive_direction": "Protect the payoff and accelerate the repeated route.",
                "pacing_assessment": "The middle needs one decisive compression.",
                "intrigue_assessment": "The objective remains clear.",
                "information_density_assessment": "Narration is appropriately sparse.",
                "continuity_assessment": "The arrival preserves causality.",
                "priority_changes": [{
                    "recommendation_id": "recommendation-1",
                    "priority": 1,
                    "action": "Shorten the setup",
                    "rationale": "The route is already legible",
                }],
                "protected_moments": [{
                    "recommendation_id": "recommendation-1",
                    "rationale": "The arrival is the payoff",
                }],
                "unresolved_questions": [],
            }

            rendered = render_editorial_html(project)
            self.assertIn("Editorial suggestion", rendered)
            self.assertNotIn("Backup option", rendered)
            self.assertNotIn("Continuity-first case", rendered)
            self.assertNotIn("Selection-first case", rendered)
            self.assertNotIn(project["sources"][0]["source_id"], rendered)
            self.assertIn("recording-001", rendered)
            self.assertIn("1h 00m–1h 30m", rendered)
            self.assertIn("Repeated &lt;route&gt;", rendered)
            self.assertNotIn("Repeated <route>", rendered)
            self.assertNotIn("Unselected detour", rendered)
            self.assertIn("Selected editorial plan", rendered)
            self.assertIn('class="card editorial-pair"', rendered)
            self.assertIn(
                "grid-template-columns: 36px minmax(0, 2fr) minmax(0, 3fr)",
                rendered,
            )
            self.assertIn(".timeline-rail { position: relative; z-index: 2; width: 108px;", rendered)
            self.assertIn('class="timeline-rail"', rendered)
            self.assertIn(
                ".editorial-primary .editorial-card-body { grid-template-columns: minmax(0, 1fr);",
                rendered,
            )
            self.assertIn(".editorial-primary { min-width: 0; padding-bottom: 72px;", rendered)
            self.assertIn(
                ".editorial-primary > .recommendation-head { margin: 18px 0 0 16px;",
                rendered,
            )
            self.assertIn(
                ".linked-card-body { position: relative; display: grid;", rendered
            )
            self.assertIn(
                ".linked-card img.editorial-frame:hover { z-index: 5; transform: scale(2.4);",
                rendered,
            )
            self.assertIn(
                ".reference-proof img:hover { width: min(620px, 100%);", rendered
            )
            self.assertIn("font-size: 18px;", rendered)
            self.assertIn("Suggested edits", rendered)
            self.assertIn("Bridge the repeated route", rendered)
            self.assertIn("Standalone narration and creative notes", rendered)
            self.assertNotIn("<h2>Narration briefs</h2>", rendered)
            self.assertNotIn("<h2>Creative editorial accents</h2>", rendered)
            self.assertIn("Director’s review", rendered)
            self.assertIn("Editorial contract", rendered)
            self.assertIn("Narrated opening, then live handoff", rendered)
            self.assertIn("Understand the premise", rendered)
            self.assertIn("Protect the payoff", rendered)
            self.assertNotIn("Highest-priority changes", rendered)
            self.assertNotIn("Moments to protect", rendered)
            self.assertNotIn("recommendation-1", rendered)

            output = root / "report.html"

            def create_frame(command, **_kwargs):
                Path(command[-1]).write_bytes(b"jpeg")
                return mock.Mock(returncode=0)

            with mock.patch("subtitler.editorial_report.subprocess.run", side_effect=create_frame) as render_frame:
                write_editorial_html(output, project)
                first_render_count = render_frame.call_count
                stale_frame = root / "report-frames" / "stale-from-prior-plan.jpg"
                stale_frame.write_bytes(b"old")
                write_editorial_html(output, project)
                self.assertEqual(render_frame.call_count, first_render_count * 2)
                self.assertFalse(stale_frame.exists())
            written = output.read_text(encoding="utf-8")
            self.assertTrue(written.startswith("<!doctype html>"))
            self.assertIn('class="editorial-frame"', written)
            self.assertIn("report-frames/recording-001.jpg", written)


if __name__ == "__main__":
    unittest.main()
