import tempfile
import json
import unittest
from pathlib import Path

from subtitler.editorial_project import (
    EditorialProjectOptions,
    EditorialSourceInput,
    create_editorial_project,
    extend_editorial_project,
    fingerprint_source,
    load_editorial_checkpoint,
    next_incomplete_source,
    relink_editorial_source,
    unresolved_editorial_sources,
    update_source_stage,
    write_editorial_checkpoint,
)
from subtitler.errors import SubtitlerError


class EditorialProjectTests(unittest.TestCase):
    def test_checkpoint_tracks_ordered_sources_and_resumes_first_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first recording")
            second.write_bytes(b"second recording")
            project = create_editorial_project(
                [EditorialSourceInput(first, 60_000), EditorialSourceInput(second, 120_000)],
                EditorialProjectOptions("A game", "Finish the run", 90_000, 150_000),
                project_id="project-test",
                now_utc="2026-01-01T00:00:00+00:00",
            )

            self.assertEqual([item["order"] for item in project["sources"]], [0, 1])
            first_id = project["sources"][0]["source_id"]
            for stage in project["sources"][0]["stages"]:
                update_source_stage(project, first_id, stage, "in_progress")
                update_source_stage(project, first_id, stage, "complete", output={"artifact": stage})
            self.assertEqual(next_incomplete_source(project)["order"], 1)

            checkpoint = root / "project.editorial.json"
            write_editorial_checkpoint(checkpoint, project)
            loaded = load_editorial_checkpoint(checkpoint)
            self.assertEqual(loaded["project_id"], "project-test")
            self.assertEqual(loaded["sources"][0]["status"], "complete")

    def test_fingerprint_survives_rename_and_relink_rejects_different_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.mp4"
            original.write_bytes(b"same media content")
            project = create_editorial_project(
                [EditorialSourceInput(original, 60_000)],
                EditorialProjectOptions("A game", "Finish the run", 30_000, 45_000),
            )
            source_id = project["sources"][0]["source_id"]
            moved = root / "renamed.mp4"
            original.rename(moved)

            self.assertEqual(unresolved_editorial_sources(project)[0]["reason"], "missing")
            relink_editorial_source(project, source_id, moved)
            self.assertEqual(unresolved_editorial_sources(project), [])
            self.assertEqual(
                fingerprint_source(moved).digest,
                project["sources"][0]["fingerprint"]["digest"],
            )

            other = root / "other.mp4"
            other.write_bytes(b"different content")
            with self.assertRaises(SubtitlerError):
                relink_editorial_source(project, source_id, other)

    def test_completed_stage_cannot_be_reopened_accidentally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("A game", "Finish the run", 30_000, 45_000),
            )
            source_id = project["sources"][0]["source_id"]
            update_source_stage(project, source_id, "source_probe", "in_progress")
            update_source_stage(project, source_id, "source_probe", "complete")

            with self.assertRaises(SubtitlerError):
                update_source_stage(project, source_id, "source_probe", "in_progress")

    def test_paired_source_persists_independent_roles_and_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facecam = root / "run-facecam.mp4"
            gameplay = root / "run-gameplay.mp4"
            facecam.write_bytes(b"voice and face video")
            gameplay.write_bytes(b"gameplay video")
            project = create_editorial_project(
                [
                    EditorialSourceInput(
                        gameplay,
                        60_000,
                        audio_path=facecam,
                        visual_path=gameplay,
                        audio_duration_ms=60_100,
                        visual_duration_ms=60_000,
                        frame_rate=60.0,
                        media_mode="paired",
                        pairing_basis="filename",
                    )
                ],
                EditorialProjectOptions("A game", "Finish the run", 30_000, 45_000),
            )

            source = project["sources"][0]
            self.assertEqual(source["media_mode"], "paired")
            self.assertEqual(source["audio_path"], str(facecam.resolve()))
            self.assertEqual(source["visual_path"], str(gameplay.resolve()))
            self.assertNotEqual(
                source["audio_fingerprint"]["digest"],
                source["visual_fingerprint"]["digest"],
            )

            moved_facecam = root / "moved-facecam.mp4"
            facecam.rename(moved_facecam)
            unresolved = unresolved_editorial_sources(project)
            self.assertEqual(unresolved[0]["role"], "audio")
            relink_editorial_source(
                project, source["source_id"], moved_facecam, role="audio"
            )
            self.assertEqual(unresolved_editorial_sources(project), [])

    def test_paired_source_rejects_more_than_ten_frames_of_duration_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facecam = root / "run-facecam.mp4"
            gameplay = root / "run-gameplay.mp4"
            facecam.write_bytes(b"face")
            gameplay.write_bytes(b"game")
            with self.assertRaisesRegex(SubtitlerError, "more than 10 frames"):
                create_editorial_project(
                    [
                        EditorialSourceInput(
                            gameplay,
                            60_000,
                            audio_path=facecam,
                            visual_path=gameplay,
                            audio_duration_ms=60_200,
                            visual_duration_ms=60_000,
                            frame_rate=60.0,
                            media_mode="paired",
                            pairing_basis="resolution",
                        )
                    ],
                    EditorialProjectOptions("A game", "Finish the run", 30_000, 45_000),
                )

    def test_load_upgrades_the_earlier_single_file_checkpoint_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.mp4"
            source_path.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source_path, 60_000)],
                EditorialProjectOptions("A game", "Finish the run", 30_000, 45_000),
            )
            project["schema_version"] = 1
            project.pop("pipeline_versions")
            source = project["sources"][0]
            for field in (
                "media_mode",
                "pairing_basis",
                "audio_path",
                "visual_path",
                "audio_original_name",
                "visual_original_name",
                "audio_duration_ms",
                "visual_duration_ms",
                "frame_rate",
                "audio_fingerprint",
                "visual_fingerprint",
            ):
                source.pop(field)
            for checkpoint_value in source["stages"].values():
                checkpoint_value.pop("version")
            project["editorial_map"]["global_reconciliation"].pop("version")
            checkpoint = root / "legacy.editorial.json"
            checkpoint.write_text(json.dumps(project), encoding="utf-8")

            loaded = load_editorial_checkpoint(checkpoint)

            self.assertEqual(loaded["schema_version"], 3)
            self.assertEqual(loaded["output_locale"], "en")
            self.assertEqual(loaded["sources"][0]["media_mode"], "single")
            self.assertEqual(loaded["pipeline_versions"]["transcription"], 1)

    def test_schema_two_checkpoint_is_migrated_and_persisted_as_english(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions("A game", "Finish the run", 30_000, 45_000),
            )
            project["schema_version"] = 2
            project.pop("output_locale")
            checkpoint = root / "legacy.editorial.json"
            checkpoint.write_text(json.dumps(project), encoding="utf-8")

            loaded = load_editorial_checkpoint(checkpoint)
            write_editorial_checkpoint(checkpoint, loaded)

            persisted = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 3)
            self.assertEqual(persisted["output_locale"], "en")

    def test_load_repairs_duplicated_source_aggregates_from_local_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            project = create_editorial_project(
                [EditorialSourceInput(source, 60_000)],
                EditorialProjectOptions(
                    "A game", "Finish the run", 30_000, 45_000, subtitle_mode="emphasis"
                ),
            )
            source_id = project["sources"][0]["source_id"]
            local_output = {
                "recommendations": [{"id": "recommendation-1"}],
                "narration_briefs": [],
                "creative_suggestions": [],
                "emphasized_phrases": [{"id": "phrase-1"}],
                "timeline_coverage": [{"start_ms": 0, "end_ms": 60_000}],
            }
            for stage in project["sources"][0]["stages"]:
                update_source_stage(project, source_id, stage, "in_progress")
                update_source_stage(
                    project,
                    source_id,
                    stage,
                    "complete",
                    output=local_output if stage == "local_reconciliation" else {"stage": stage},
                )
            project["editorial_map"]["emphasized_phrases"] = [
                {"id": "phrase-1"},
                {"id": "phrase-1"},
            ]
            project["editorial_map"]["timeline_coverage"] = [
                {"start_ms": 0, "end_ms": 60_000},
                {"start_ms": 0, "end_ms": 60_000},
            ]
            checkpoint = root / "duplicated.editorial.json"
            write_editorial_checkpoint(checkpoint, project)

            loaded = load_editorial_checkpoint(checkpoint)

            self.assertEqual(loaded["editorial_map"]["emphasized_phrases"], [{"id": "phrase-1"}])
            self.assertEqual(
                loaded["editorial_map"]["timeline_coverage"],
                [{"start_ms": 0, "end_ms": 60_000}],
            )

    def test_followup_extension_preserves_completed_source_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            followup = root / "followup.mp4"
            first.write_bytes(b"first")
            followup.write_bytes(b"followup")
            options = EditorialProjectOptions("Run", "Finish", 30_000, 90_000)
            project = create_editorial_project([EditorialSourceInput(first, 60_000)], options)
            source_id = project["sources"][0]["source_id"]
            for stage in project["sources"][0]["stages"]:
                update_source_stage(project, source_id, stage, "in_progress")
                update_source_stage(project, source_id, stage, "complete", output={"stage": stage})
            original_result = {"recommendations": [{"id": "preserved"}]}
            project["sources"][0]["result"] = original_result

            extend_editorial_project(project, [EditorialSourceInput(followup, 45_000)], options)

            self.assertEqual(len(project["sources"]), 2)
            self.assertIs(project["sources"][0]["result"], original_result)
            self.assertEqual(project["sources"][1]["status"], "pending")
            self.assertEqual(project["sources"][1]["order"], 1)


if __name__ == "__main__":
    unittest.main()
