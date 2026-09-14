import json
from unittest.mock import patch

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from subtitler.editorial_project import (
    CHECKPOINT_STAGES,
    EditorialProjectOptions,
    EditorialSourceInput,
    create_editorial_project,
    load_editorial_checkpoint,
    write_editorial_checkpoint,
)
from subtitler.operation_store import ArtifactError
from subtitler.editorial_runner import (
    EditorialRunInterrupted,
    _record_stage_cost,
    run_editorial_project,
)


class _RecordingExecutor:
    def __init__(self, fail_once: tuple[int, str] | None = None) -> None:
        self.calls: list[tuple[int, str, list[str]]] = []
        self.fail_once = fail_once

    def run_stage(self, stage, source, project, prior_outputs):
        self.calls.append((source["order"], stage, sorted(prior_outputs)))
        if self.fail_once == (source["order"], stage):
            self.fail_once = None
            raise RuntimeError("vision provider disconnected")
        if stage == "semantic_spans":
            return {
                "cumulative_context": {
                    **project["cumulative_context"],
                    "open_threads": [f"thread-through-source-{source['order']}"],
                }
            }
        if stage == "local_reconciliation":
            return {
                "recommendations": [{"id": f"recommendation-{source['order']}"}],
                "narration_briefs": [],
                "connections": [],
                "global_threads": [],
                "conflicts": [],
            }
        return {"stage": stage, "source_order": source["order"]}

    def finalize_project(self, project):
        self.finalized_with = [item["source_id"] for item in project["sources"]]
        return {
            "global_threads": [{"title": "Project thread"}],
            "connections": [],
            "conflicts": [],
            "duration_budget": {"target_min_ms": project["target_duration_min_ms"]},
            "editorial_direction_summary": "Use the single strongest edit at each location.",
            "optimal_plan": [],
        }

    def plan_actions(self, project):
        return {
            "director_review": {},
            "director_model": "test",
            "final_actions": [],
            "supporting_edits": [],
            "editorial_threads": [],
            "story_actions": [],
        }

    def resolve_assets(self, project):
        return {"supporting_edits": [], "editorial_assets": []}


class EditorialRunnerTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        sources = []
        for index in range(2):
            path = root / f"source-{index}.mp4"
            path.write_bytes(f"media-{index}".encode())
            sources.append(EditorialSourceInput(path, 60_000))
        project = create_editorial_project(
            sources,
            EditorialProjectOptions("Recording", "Find the throughline", 30_000, 90_000),
        )
        checkpoint = root / "project.editorial.json"
        write_editorial_checkpoint(checkpoint, project)
        return checkpoint

    def test_completed_results_are_verified_and_only_changed_operations_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            checkpoint = self._project(Path(directory))
            executor = _RecordingExecutor()
            executor.operation_parameters = lambda stage: {"selection": 1} if stage == "action_planning" else {}
            before = run_editorial_project(checkpoint, executor)
            executor.calls.clear()
            before["sources"][0]["reference_frames"] = [{"timestamp_ms": 1000, "path": "report-thumbnail.jpg"}]
            write_editorial_checkpoint(checkpoint, before)
            with patch.object(executor, "plan_actions", side_effect=AssertionError("completed operation reran")):
                run_editorial_project(checkpoint, executor)
            self.assertEqual(executor.calls, [])
            executor.operation_parameters = lambda stage: {"selection": 2} if stage == "action_planning" else {}
            after = run_editorial_project(checkpoint, executor)
            self.assertEqual(executor.calls, [])
            self.assertEqual(before["editorial_map"]["global_reconciliation"], after["editorial_map"]["global_reconciliation"])
            self.assertNotEqual(before["editorial_map"]["action_planning"]["operation_result"],
                                after["editorial_map"]["action_planning"]["operation_result"])
            after["sources"][0]["stages"]["source_probe"]["output"]["stage"] = "tampered"
            write_editorial_checkpoint(checkpoint, after)
            with self.assertRaises(ArtifactError):
                run_editorial_project(checkpoint, executor)

    def test_result_survives_crash_between_operation_and_checkpoint_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            checkpoint = self._project(Path(directory))
            executor = _RecordingExecutor()
            def crash(path, project):
                if project["sources"][0]["stages"]["source_probe"]["status"] == "complete":
                    raise OSError("checkpoint disk interruption")
                write_editorial_checkpoint(path, project)
            with patch("subtitler.editorial_runner.write_editorial_checkpoint", side_effect=crash):
                with self.assertRaises(OSError):
                    run_editorial_project(checkpoint, executor)
            executor.calls.clear()
            run_editorial_project(checkpoint, executor)
            self.assertNotIn((0, "source_probe"), [(order, stage) for order, stage, _ in executor.calls])

    def test_transcription_reuses_saved_results_after_admission_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            checkpoint = self._project(Path(directory))
            executor = _RecordingExecutor()
            cost = {"max_estimated_api_cost_usd": 10, "allow_api_spend": True, "estimate_cost_only": False}
            executor.operation_parameters = lambda stage: {"cost": dict(cost)} if stage == "transcription" else {}
            before = run_editorial_project(checkpoint, executor)
            cost.update(max_estimated_api_cost_usd=25, allow_api_spend=False)
            executor.calls.clear()
            after = run_editorial_project(checkpoint, executor)
            self.assertEqual(executor.calls, [])
            self.assertEqual(before["sources"][0]["stages"]["transcription"],
                             after["sources"][0]["stages"]["transcription"])
            cost["estimate_cost_only"] = True
            run_editorial_project(checkpoint, executor)
            self.assertIn((0, "transcription"), [(order, stage) for order, stage, _ in executor.calls])

    def test_transcription_crash_recovery_keeps_original_artifact_key_after_budget_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            checkpoint = self._project(Path(directory))
            executor = _RecordingExecutor()
            cost = {"max_estimated_api_cost_usd": 10, "estimate_cost_only": False}
            executor.operation_parameters = lambda stage: {"cost": dict(cost)} if stage == "transcription" else {}

            def crash(path, project):
                if project["sources"][0]["stages"]["transcription"]["status"] == "complete":
                    raise OSError("checkpoint disk interruption")
                write_editorial_checkpoint(path, project)

            with patch("subtitler.editorial_runner.write_editorial_checkpoint", side_effect=crash):
                with self.assertRaises(OSError):
                    run_editorial_project(checkpoint, executor)
            executor.calls.clear()
            cost["max_estimated_api_cost_usd"] = 25
            run_editorial_project(checkpoint, executor)
            self.assertNotIn((0, "transcription"), [(order, stage) for order, stage, _ in executor.calls])
            self.assertIn((1, "transcription"), [(order, stage) for order, stage, _ in executor.calls])

    def test_failed_editorial_usage_survives_a_successful_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            checkpoint = self._project(Path(directory))
            executor = _RecordingExecutor()
            error = RuntimeError("provider disconnected after billing")
            error.editorial_failure_output = {"api_cost_usd": 0.1, "api_usage": []}
            with patch.object(executor, "plan_actions", side_effect=error):
                with self.assertRaises(EditorialRunInterrupted):
                    run_editorial_project(checkpoint, executor)
            run_editorial_project(checkpoint, executor)
            failures = [json.loads(path.read_text(encoding="utf-8"))
                        for path in checkpoint.with_suffix(".operations").glob("*.failed.*.json")]
            self.assertEqual(failures[0]["failure"]["api_cost_usd"], 0.1)

    def test_invalid_project_result_is_a_durable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            checkpoint = self._project(Path(directory))
            executor = _RecordingExecutor()
            with patch.object(executor, "plan_actions", return_value=None):
                with self.assertRaises(EditorialRunInterrupted):
                    run_editorial_project(checkpoint, executor)
            failed = load_editorial_checkpoint(checkpoint)
            self.assertEqual(failed["editorial_map"]["action_planning"]["status"], "failed")
            executor.calls.clear()
            run_editorial_project(checkpoint, executor)
            self.assertEqual(executor.calls, [])

    def test_processes_every_stage_of_one_source_before_the_next(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = self._project(root)
            executor = _RecordingExecutor()

            log = StringIO()
            with redirect_stdout(log):
                result = run_editorial_project(checkpoint, executor)

            expected = [(0, stage) for stage in CHECKPOINT_STAGES] + [(1, stage) for stage in CHECKPOINT_STAGES]
            self.assertEqual([(order, stage) for order, stage, _ in executor.calls], expected)
            self.assertEqual(result["editorial_map"]["status"], "complete")
            self.assertEqual(result["editorial_map"]["global_reconciliation"]["status"], "complete")
            self.assertEqual(result["editorial_map"]["action_planning"]["status"], "complete")
            self.assertEqual(len(result["editorial_map"]["recommendations"]), 2)
            self.assertTrue(checkpoint.with_suffix(".html").is_file())
            self.assertTrue(checkpoint.with_suffix(".exo").is_file())
            self.assertEqual(result["outputs"]["exo_path"], str(checkpoint.with_suffix(".exo")))
            self.assertIn("Editorial stage 1/8", log.getvalue())
            self.assertIn("Factual story synthesis", log.getvalue())
            self.assertEqual(result["editorial_map"]["editorial_assets"]["status"], "complete")
            self.assertIn("Editorial run complete", log.getvalue())
            executor.calls.clear()
            destination = root / "deliverables" / "guide.exo"
            with patch("subtitler.editorial_audio.prepare_editorial_audio") as audio:
                run_editorial_project(checkpoint, executor, exo_path=destination,
                                      report_path=destination.with_suffix(".html"))
            audio.assert_called_once()
            self.assertEqual(audio.call_args.args[1], destination.with_suffix(".audio"))
            self.assertTrue(destination.is_file())
            self.assertTrue(destination.with_suffix(".html").is_file())
            self.assertEqual(executor.calls, [])

    def test_failure_checkpoints_and_retry_skips_completed_expensive_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = self._project(root)
            first_executor = _RecordingExecutor(fail_once=(0, "visual_learning"))

            with self.assertRaises(EditorialRunInterrupted):
                run_editorial_project(checkpoint, first_executor)
            interrupted = load_editorial_checkpoint(checkpoint)
            first = interrupted["sources"][0]
            self.assertEqual(first["stages"]["transcription"]["status"], "complete")
            self.assertEqual(first["stages"]["visual_learning"]["status"], "failed")

            retry_executor = _RecordingExecutor()
            result = run_editorial_project(checkpoint, retry_executor)

            self.assertNotIn((0, "source_probe"), [(order, stage) for order, stage, _ in retry_executor.calls])
            self.assertNotIn((0, "transcription"), [(order, stage) for order, stage, _ in retry_executor.calls])
            self.assertEqual(result["editorial_map"]["status"], "complete")

    def test_second_source_receives_context_from_first_without_boundary_special_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = self._project(Path(directory))

            class ContextExecutor(_RecordingExecutor):
                def run_stage(self, stage, source, project, prior_outputs):
                    if source["order"] == 1 and stage == "source_probe":
                        self.context_at_second_source = list(project["cumulative_context"]["open_threads"])
                    return super().run_stage(stage, source, project, prior_outputs)

            executor = ContextExecutor()
            run_editorial_project(checkpoint, executor)

            self.assertEqual(executor.context_at_second_source, ["thread-through-source-0"])

    def test_global_reconciliation_failure_resumes_without_reprocessing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = self._project(Path(directory))

            class FailingGlobalExecutor(_RecordingExecutor):
                def finalize_project(self, project):
                    raise RuntimeError("global request timed out")

            with self.assertRaises(EditorialRunInterrupted):
                run_editorial_project(checkpoint, FailingGlobalExecutor())
            interrupted = load_editorial_checkpoint(checkpoint)
            self.assertEqual(interrupted["editorial_map"]["global_reconciliation"]["status"], "failed")
            self.assertTrue(all(source["status"] == "complete" for source in interrupted["sources"]))

            retry = _RecordingExecutor()
            result = run_editorial_project(checkpoint, retry)
            self.assertEqual(retry.calls, [])
            self.assertEqual(result["editorial_map"]["global_reconciliation"]["status"], "complete")

    def test_stage_cost_replaces_the_prior_attempt_in_end_to_end_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = self._project(Path(directory))
            project = load_editorial_checkpoint(checkpoint)
            source_id = project["sources"][0]["source_id"]

            with redirect_stdout(StringIO()):
                _record_stage_cost(
                    project, source_id, "transcription", {"api_cost_usd": 1.25}
                )
                _record_stage_cost(
                    project, source_id, "transcription", {"api_cost_usd": 0.75}
                )
                _record_stage_cost(
                    project, "project", "action_planning", {"api_cost_usd": 2.0}
                )

            self.assertEqual(project["run_provenance"]["actual_cost_usd"], 2.75)
            self.assertEqual(len(project["run_provenance"]["runs"]), 2)


if __name__ == "__main__":
    unittest.main()
