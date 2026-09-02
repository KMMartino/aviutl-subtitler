from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.media_analysis import MediaAnalysisResult
from tools.reference_visual_learning import analyze_video, run


def _artifact(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    media = root / "demo.mkv"
    media.write_bytes(b"media")
    path = root / "preprocessing.json"
    path.write_text(json.dumps({
        "video_id": "demo",
        "media_path": str(media),
        "source_fingerprint": {"media_size": 5},
        "probe": {"duration_ms": 10_000},
        "samples": [{"timestamp_sec": 0.0}],
        "frame_differences": [],
    }), encoding="utf-8")
    return path


class ReferenceVisualLearningTests(unittest.TestCase):
    @patch("tools.reference_visual_learning._analyze_editorial_visual_windows")
    def test_wraps_helper_and_persists_identity_and_paths(self, helper) -> None:
        helper.return_value = MediaAnalysisResult("summary", ["gameplay"], [], "openai", "gpt-5.6-luna", "v", 1, 3, 2, 0.01, [{"left_ms": 0}])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            payload = analyze_video(_artifact(root / "input"), root / "output")
            self.assertEqual(payload["status"], "complete")
            self.assertTrue(payload["cache_identity"])
            self.assertTrue(payload["window_progress_path"].endswith("visual.window_progress.json"))
            self.assertEqual(helper.call_args.kwargs["max_workers"], 1)

    @patch("tools.reference_visual_learning._analyze_editorial_visual_windows")
    def test_matching_checkpoint_is_reused(self, helper) -> None:
        helper.return_value = MediaAnalysisResult("summary", [], [], "openai", "gpt-5.6-luna", "v", 1, 0, 0, 0.0, [])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            artifact = _artifact(root / "input")
            first = analyze_video(artifact, root / "output")
            second = analyze_video(artifact, root / "output")
            self.assertEqual(first, second)
            helper.assert_called_once()

    @patch("tools.reference_visual_learning.analyze_video")
    def test_only_filter_and_worker_cap(self, analyze) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _artifact(root / "artifacts" / "demo")
            run(root / "artifacts", root / "out", workers=99, only={"demo"})
            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(analyze.call_args.args[0].parent.name, "demo")

    @patch("tools.reference_visual_learning.analyze_video")
    def test_total_request_concurrency_is_capped(self, analyze) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _artifact(root / "artifacts" / "one")
            _artifact(root / "artifacts" / "two")
            run(root / "artifacts", root / "out", workers=2, window_workers=99)
            self.assertEqual(analyze.call_count, 2)
            self.assertEqual({call.kwargs["workers"] for call in analyze.call_args_list}, {2})


if __name__ == "__main__":
    unittest.main()
