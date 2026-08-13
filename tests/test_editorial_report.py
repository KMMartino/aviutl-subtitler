import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.editorial_project import EditorialProjectOptions, EditorialSourceInput, create_editorial_project
from subtitler.editorial_report import render_editorial_html, write_editorial_html


class EditorialReportTests(unittest.TestCase):
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

            rendered = render_editorial_html(project)

            self.assertIn('<html lang="ja">', rendered)
            self.assertIn("目標時間", rendered)
            self.assertIn("編集提案", rendered)
            self.assertIn("編集マーカーのないタイムライン区間", rendered)
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
            self.assertIn("Linked narration and accents", rendered)
            self.assertIn("Bridge the repeated route", rendered)
            self.assertIn("Standalone narration and creative notes", rendered)
            self.assertNotIn("<h2>Narration briefs</h2>", rendered)
            self.assertNotIn("<h2>Creative editorial accents</h2>", rendered)
            self.assertIn("Director’s review", rendered)
            self.assertIn("Protect the payoff", rendered)
            self.assertIn("Moments to protect", rendered)
            self.assertIn("The arrival is the payoff", rendered)
            self.assertNotIn("recommendation-1", rendered)

            output = root / "report.html"

            def create_frame(command, **_kwargs):
                Path(command[-1]).write_bytes(b"jpeg")
                return mock.Mock(returncode=0)

            with mock.patch("subtitler.editorial_report.subprocess.run", side_effect=create_frame):
                write_editorial_html(output, project)
            written = output.read_text(encoding="utf-8")
            self.assertTrue(written.startswith("<!doctype html>"))
            self.assertIn('class="editorial-frame"', written)
            self.assertIn("report-frames/recording-001.jpg", written)


if __name__ == "__main__":
    unittest.main()
