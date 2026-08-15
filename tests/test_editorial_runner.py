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
from subtitler.editorial_runner import EditorialRunInterrupted, run_editorial_project


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
            self.assertEqual(len(result["editorial_map"]["recommendations"]), 2)
            self.assertTrue(checkpoint.with_suffix(".html").is_file())
            self.assertTrue(checkpoint.with_suffix(".exo").is_file())
            self.assertEqual(result["outputs"]["exo_path"], str(checkpoint.with_suffix(".exo")))
            self.assertIn("Editorial stage 1/7", log.getvalue())
            self.assertIn("Project-wide synthesis", log.getvalue())
            self.assertEqual(result["editorial_map"]["editorial_assets"]["status"], "complete")
            self.assertIn("Editorial run complete", log.getvalue())

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


if __name__ == "__main__":
    unittest.main()
