import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.editorial_project import EditorialProjectOptions, EditorialSourceInput, create_editorial_project, write_editorial_checkpoint
from subtitler.editorial_project_cli import main


class EditorialProjectCliTests(unittest.TestCase):
    def test_apply_cuts_wires_hosted_narration_review_and_reports_its_result(self) -> None:
        class Provider:
            closed = False

            def close(self) -> None:
                self.closed = True

        provider = Provider()

        class Executor:
            def __init__(self, options) -> None:
                self.options = options

            def build_narration_review_provider(self, usage, sidecar_base):
                self.usage = usage
                self.sidecar_base = sidecar_base
                return provider

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reviewed = root / "review.exo"
            output = io.StringIO()
            with (
                patch(
                    "subtitler.editorial_project_cli.HostedEditorialStageExecutor",
                    Executor,
                ),
                patch(
                    "subtitler.editorial_project_cli.apply_reviewed_editorial_cuts",
                    return_value={
                        "review_project": str(reviewed),
                        "checkpoint": str(root / "review.json"),
                        "output_path": str(root / "review-cuts-applied.exo"),
                        "report_path": str(root / "review-cuts-applied.html"),
                        "cut_count": 2,
                        "removed_ms": 3000,
                        "ignored_short_count": 0,
                        "narration_brief_count": 1,
                        "narration_reference_count": 3,
                    },
                ) as apply_cuts,
                contextlib.redirect_stdout(output),
            ):
                code = main([
                    "apply-cuts",
                    "--review-project", str(reviewed),
                    "--config", str(root / "hosted-long-stream.json"),
                    "--env-file", str(root / ".env"),
                    "--workspace", str(root / "review.files"),
                ])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["narration_reference_count"], 3)
        self.assertEqual(payload["api_cost_usd"], 0)
        self.assertTrue(provider.closed)
        self.assertIs(apply_cuts.call_args.kwargs["narration_provider"], provider)

    def test_status_reports_durable_stage_state_and_unresolved_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("Recording", "Find the story", 30_000, 45_000),
            )
            checkpoint = root / "project.editorial.json"
            write_editorial_checkpoint(checkpoint, project)
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["status", "--checkpoint", str(checkpoint)])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "pending")
            self.assertEqual(payload["sources"][0]["stages"]["transcription"], "pending")
            self.assertEqual(payload["unresolved_sources"], [])

    def test_init_accepts_a_resolved_facecam_gameplay_source_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facecam = root / "run-facecam.mp4"
            gameplay = root / "run-gameplay.mp4"
            facecam.write_bytes(b"facecam")
            gameplay.write_bytes(b"gameplay")
            checkpoint = root / "project.editorial.json"
            spec = json.dumps(
                {
                    "mode": "paired",
                    "audioPath": str(facecam),
                    "visualPath": str(gameplay),
                    "frameRate": 60,
                    "pairingBasis": "filename",
                    "roleConfirmed": True,
                }
            )
            with (
                patch(
                    "subtitler.editorial_project_cli.get_media_duration",
                    side_effect=[60.1, 60.0],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "init",
                        "--checkpoint",
                        str(checkpoint),
                        "--source-spec",
                        spec,
                        "--title",
                        "Recording",
                        "--objective",
                        "Find the story",
                        "--target-min-sec",
                        "30",
                        "--target-max-sec",
                        "45",
                        "--output-locale",
                        "ja",
                    ]
                )
            project = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(project["sources"][0]["audio_path"], str(facecam.resolve()))
            self.assertEqual(project["sources"][0]["visual_path"], str(gameplay.resolve()))
            self.assertEqual(project["output_locale"], "ja")

    def test_inspect_reports_safe_reuse_choices_for_matching_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("Recording", "Find the story", 30_000, 45_000),
            )
            checkpoint = root / "project.editorial.json"
            write_editorial_checkpoint(checkpoint, project)
            output = io.StringIO()
            spec = json.dumps(
                {
                    "mode": "single",
                    "audioPath": str(source),
                    "visualPath": str(source),
                }
            )

            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "inspect",
                        "--checkpoint",
                        str(checkpoint),
                        "--source-spec",
                        spec,
                    ]
                )

            inspection = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(inspection["matches_sources"])
            self.assertEqual(inspection["recommended_restart_from"], "compatible")

    def test_inspect_without_selected_sources_opens_an_existing_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("Recovered", "Continue", 30_000, 45_000),
            )
            checkpoint = root / "project.editorial.json"
            write_editorial_checkpoint(checkpoint, project)
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["inspect", "--checkpoint", str(checkpoint)])

            inspection = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(inspection["match_kind"], "full")
            self.assertEqual(inspection["project_request"]["titleOrGame"], "Recovered")


if __name__ == "__main__":
    unittest.main()
