import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.editorial_project import (
    CHECKPOINT_STAGES,
    EDITORIAL_STAGE_VERSIONS,
    EditorialProjectOptions,
    EditorialSourceInput,
    create_editorial_project,
    unresolved_editorial_sources,
    update_source_stage,
)
from subtitler.editorial_resume import (
    first_incompatible_boundary,
    inspect_editorial_resume,
    prepare_editorial_resume,
    relink_matching_editorial_sources,
)


class EditorialResumeTests(unittest.TestCase):
    def test_version_mismatch_restarts_at_first_changed_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = _completed_project(Path(directory))
            with patch.dict(
                EDITORIAL_STAGE_VERSIONS,
                {
                    "semantic_spans": EDITORIAL_STAGE_VERSIONS["semantic_spans"] + 1,
                    "local_reconciliation": EDITORIAL_STAGE_VERSIONS["local_reconciliation"] + 1,
                    "global_reconciliation": EDITORIAL_STAGE_VERSIONS["global_reconciliation"] + 1,
                },
            ):
                self.assertEqual(first_incompatible_boundary(project), "semantic_spans")
                selected = prepare_editorial_resume(project, "compatible")

            self.assertEqual(selected, "semantic_spans")
            source = project["sources"][0]
            self.assertEqual(source["stages"]["visual_learning"]["status"], "complete")
            self.assertEqual(source["stages"]["semantic_spans"]["status"], "pending")
            self.assertEqual(source["stages"]["local_reconciliation"]["status"], "pending")
            self.assertEqual(project["editorial_map"]["global_reconciliation"]["status"], "pending")
            self.assertEqual(
                project["pipeline_versions"]["semantic_spans"],
                EDITORIAL_STAGE_VERSIONS["semantic_spans"] + 1,
            )

    def test_first_boundary_mismatch_requires_a_full_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = _completed_project(Path(directory))
            with patch.dict(
                EDITORIAL_STAGE_VERSIONS,
                {"source_probe": EDITORIAL_STAGE_VERSIONS["source_probe"] + 1},
            ):
                selected = prepare_editorial_resume(project, "compatible")

            self.assertEqual(selected, "source_probe")
            self.assertTrue(
                all(
                    checkpoint["status"] == "pending"
                    for checkpoint in project["sources"][0]["stages"].values()
                )
            )

    def test_compatible_resume_preserves_exact_failed_stage_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = _project(root)
            source_id = project["sources"][0]["source_id"]
            update_source_stage(project, source_id, "source_probe", "in_progress")
            update_source_stage(project, source_id, "source_probe", "complete", output={"probe": 1})
            update_source_stage(project, source_id, "transcription", "in_progress")
            update_source_stage(project, source_id, "transcription", "failed", error="network")

            selected = prepare_editorial_resume(project, "compatible")

            self.assertIsNone(selected)
            self.assertEqual(project["sources"][0]["stages"]["source_probe"]["status"], "complete")
            self.assertEqual(project["sources"][0]["stages"]["transcription"]["status"], "failed")

    def test_successful_checkpoint_can_rerun_only_global_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = _completed_project(Path(directory))
            selected = prepare_editorial_resume(project, "global_reconciliation")

            self.assertEqual(selected, "global_reconciliation")
            self.assertTrue(
                all(
                    checkpoint["status"] == "complete"
                    for checkpoint in project["sources"][0]["stages"].values()
                )
            )
            self.assertEqual(project["editorial_map"]["global_reconciliation"]["status"], "pending")

    def test_successful_checkpoint_can_rerun_only_reference_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = _completed_project(Path(directory))
            project["editorial_map"]["assets"] = [{"asset_id": "asset-001"}]

            selected = prepare_editorial_resume(project, "editorial_assets")

            self.assertEqual(selected, "editorial_assets")
            self.assertEqual(project["editorial_map"]["global_reconciliation"]["status"], "complete")
            self.assertEqual(project["editorial_map"]["editorial_assets"]["status"], "pending")
            self.assertEqual(project["editorial_map"]["assets"], [])
            self.assertTrue(
                all(
                    checkpoint["status"] == "complete"
                    for checkpoint in project["sources"][0]["stages"].values()
                )
            )

    def test_local_restart_clears_emphasis_and_coverage_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = _completed_project(Path(directory))
            project["editorial_map"]["emphasized_phrases"] = [{"id": "stale-phrase"}]
            project["editorial_map"]["timeline_coverage"] = [{"status": "stale"}]

            prepare_editorial_resume(project, "local_reconciliation")

            self.assertEqual(project["editorial_map"]["emphasized_phrases"], [])
            self.assertEqual(project["editorial_map"]["timeline_coverage"], [])

    def test_inspection_matches_selected_media_by_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = _completed_project(root)
            source = project["sources"][0]
            inspection = inspect_editorial_resume(
                project,
                [
                    {
                        "mode": "single",
                        "audioPath": source["audio_path"],
                        "visualPath": source["visual_path"],
                    }
                ],
            )

            self.assertTrue(inspection["matches_sources"])
            self.assertEqual(inspection["recommended_restart_from"], "global_reconciliation")
            self.assertIn("compatible", inspection["available_restart_from"])

    def test_matching_selected_media_relinks_a_moved_source_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = _project(root)
            original = Path(project["sources"][0]["visual_path"])
            moved = root / "moved-recording.mp4"
            original.rename(moved)

            relink_matching_editorial_sources(
                project,
                [{"mode": "single", "audioPath": str(moved), "visualPath": str(moved)}],
            )

            self.assertEqual(project["sources"][0]["path"], str(moved.resolve()))
            self.assertEqual(unresolved_editorial_sources(project), [])

    def test_partial_inspection_recovers_project_fields_and_identifies_followup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "part-one.mp4"
            second = root / "part-two.mp4"
            followup = root / "part-three.mp4"
            for path in (first, second, followup):
                path.write_bytes(path.name.encode())
            project = create_editorial_project(
                [EditorialSourceInput(first, 60_000), EditorialSourceInput(second, 60_000)],
                EditorialProjectOptions("Recovered title", "Recovered objective", 60_000, 90_000),
            )

            inspection = inspect_editorial_resume(
                project,
                [
                    {"mode": "single", "audioPath": str(first), "visualPath": str(first)},
                    {"mode": "single", "audioPath": str(followup), "visualPath": str(followup)},
                ],
            )

            self.assertEqual(inspection["match_kind"], "partial")
            self.assertEqual(inspection["matched_selected_indices"], [0])
            self.assertEqual(inspection["project_request"]["titleOrGame"], "Recovered title")
            self.assertEqual(inspection["project_request"]["outputLocale"], "en")
            self.assertEqual(len(inspection["project_request"]["sources"]), 2)


def _project(root: Path) -> dict:
    source = root / "recording.mp4"
    source.write_bytes(b"recording")
    return create_editorial_project(
        [EditorialSourceInput(source, 60_000)],
        EditorialProjectOptions("A game", "Finish", 30_000, 45_000),
    )


def _completed_project(root: Path) -> dict:
    project = _project(root)
    source_id = project["sources"][0]["source_id"]
    for stage in CHECKPOINT_STAGES:
        update_source_stage(project, source_id, stage, "in_progress")
        update_source_stage(project, source_id, stage, "complete", output={"stage": stage})
    global_checkpoint = project["editorial_map"]["global_reconciliation"]
    global_checkpoint.update(
        {
            "status": "complete",
            "attempts": 1,
            "completed_at_utc": "2026-01-01T00:00:00+00:00",
            "output": {"plan": True},
        }
    )
    asset_checkpoint = project["editorial_map"]["editorial_assets"]
    asset_checkpoint.update(
        {
            "status": "complete",
            "attempts": 1,
            "completed_at_utc": "2026-01-01T00:00:00+00:00",
            "output": {"editorial_assets": []},
        }
    )
    project["editorial_map"]["status"] = "complete"
    return project


if __name__ == "__main__":
    unittest.main()
