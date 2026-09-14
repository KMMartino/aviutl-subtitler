import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.api_usage import ApiUsageLedger
from subtitler.broll import CatalogAsset
from subtitler.broll_stage import BrollStageRequest, run_broll_stage
from subtitler.config import load_workflow_config
from subtitler.models import ExoSettings, Subtitle
from subtitler.operation_store import ArtifactError, OperationStore
from subtitler.review_exchange import ReviewError


class BrollResumeTests(unittest.TestCase):
    def test_interrupted_review_reuses_planning_and_retains_cost_even_if_library_description_changes(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            media = root / "battle.mp4"
            media.write_bytes(b"media fixture")
            asset = CatalogAsset("asset", media, "video", "Battle gameplay", "Battle gameplay", 10, False, (),
                                 description_source="inferred", width=1920, height=1080)
            config = load_workflow_config("hosted")
            config["broll"]["discover_web_assets"] = False
            request = BrollStageRequest("automatic", config, root / "library.sqlite", [Subtitle(0, 2, "Battle gameplay")],
                                       ExoSettings(), "stdio-v1", revision_directory=root, input_revision="subtitle-1")
            needs = {"needs": [{"start_line": 1, "end_line": 1, "description": "battle gameplay",
                                "search_terms": ["battle gameplay"], "preferred_media": "video", "need_score": .9}],
                     "protected_ranges": []}
            draft = {"placements": [], "filename_review_candidates": [{"start_line": 1, "end_line": 1,
                     "asset_id": "asset", "confidence": .8, "reason": "Title matches"}], "missing_assets": []}
            final = {"placements": [{"start_line": 1, "end_line": 1, "asset_id": "asset", "source_start_sec": 0,
                     "source_end_sec": 2, "need_score": .9, "relevance_score": .9, "placement_safety_score": .9,
                     "source_grounding_score": .9, "technical_quality_score": .9, "reason": "Reviewed battle"}],
                     "missing_assets": []}
            first_usage = ApiUsageLedger()
            first = _Provider(first_usage, needs, draft)
            with (patch("subtitler.broll_session.load_catalog", return_value=[asset]),
                  patch("subtitler.broll_session.build_broll_provider", return_value=first),
                  patch("sys.stdin", io.StringIO()), self.assertRaises(ReviewError)):
                run_broll_stage(request, first_usage)
            self.assertEqual(first.operations, ["broll_needs", "broll_placement"])
            self.assertTrue(first.closed)
            pending = json.loads((root / "broll-review.json").read_text(encoding="utf-8"))
            response = {"type": "broll-review-result", "reviewId": pending["request"]["reviewId"],
                        "decisions": [{"candidateId": pending["request"]["candidates"][0]["id"],
                                       "decision": "describe", "description": "Battle gameplay with a boss"}]}
            second_usage = ApiUsageLedger()
            second = _Provider(second_usage, final)
            with (patch("subtitler.broll_session.load_catalog", side_effect=AssertionError("snapshot must survive library edits")),
                  patch("subtitler.broll_session.build_broll_provider", return_value=second),
                  patch("sys.stdin", io.StringIO(json.dumps(response)))):
                outcome = run_broll_stage(request, second_usage)
            self.assertEqual(second.operations, ["broll_placement"])
            self.assertEqual(len(outcome.placements), 1)
            self.assertAlmostEqual(second_usage.total_cost_usd, .3)
            replay_usage = ApiUsageLedger()
            with (patch("subtitler.broll_session.build_broll_provider", side_effect=AssertionError("paid calls repeated")),
                  patch("sys.stdin.readline", side_effect=AssertionError("submitted review lost"))):
                replay = run_broll_stage(request, replay_usage)
            self.assertEqual(replay, outcome)
            self.assertAlmostEqual(replay_usage.total_cost_usd, .3)
            media.write_bytes(b"changed source")
            with self.assertRaisesRegex(ArtifactError, "changed since planning"):
                run_broll_stage(request, ApiUsageLedger())

    def test_operation_references_verify_external_output_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "transcript.txt"
            output.write_text("speech", encoding="utf-8")
            store = OperationStore(root / "operations", "revision", ApiUsageLedger())
            store.execute("transcribe", 1, {}, lambda: {"path": str(output)}, dict,
                          output_files=lambda value: [Path(value["path"])])
            reference = store.reference("transcribe", 1, {})
            self.assertEqual(store.resolve(reference), {"path": str(output)})
            output.write_text("replaced", encoding="utf-8")
            with self.assertRaisesRegex(ArtifactError, "output file changed"):
                store.resolve(reference)

    def test_operation_failures_preserve_usage_and_changed_inputs_only_rerun_their_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usage = ApiUsageLedger()
            store = OperationStore(root, "revision", usage)
            def fail():
                usage.add(provider="test", model="test", operation="draft", cost_usd=.1)
                raise RuntimeError("failed after billing")
            with self.assertRaises(RuntimeError):
                store.execute("draft", 1, {"input": 1}, fail, str)
            recovered_usage = ApiUsageLedger()
            recovered = OperationStore(root, "revision", recovered_usage)
            self.assertAlmostEqual(recovered_usage.total_cost_usd, .1)
            self.assertEqual(recovered.execute("draft", 1, {"input": 1}, lambda: "first", str), "first")
            self.assertEqual(recovered.execute("draft", 1, {"input": 2}, lambda: "second", str), "second")
            with patch("builtins.input", side_effect=AssertionError("unexpected operation")):
                self.assertEqual(recovered.execute("draft", 1, {"input": 1}, input, str), "first")
            artifact = next(root.glob("*.json"))
            artifact.write_text(artifact.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 99'), encoding="utf-8")
            with self.assertRaises(ArtifactError):
                OperationStore(root, "revision", ApiUsageLedger())


class _Provider:
    provider, model = "test", "fixture"
    def __init__(self, usage, *responses):
        self.usage, self.responses, self.operations, self.closed = usage, list(responses), [], False
    def complete(self, prompt, *, operation, response_schema=None):
        self.operations.append(operation)
        self.usage.add(provider="test", model="fixture", operation=operation, cost_usd=.1)
        return json.dumps(self.responses.pop(0))
    def close(self):
        self.closed = True
