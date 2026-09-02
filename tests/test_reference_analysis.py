from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.reference_analysis import (
    _transcript_excerpt,
    _visual_learning_context,
    analyze_video,
    run,
)


def _answer() -> dict:
    return {
        "video_summary": "A challenge run with an edited opening.",
        "format": "narrated_open",
        "opening": "Narrated setup followed by selective gameplay.",
        "narration_usage": "opening_only",
        "gameplay_editing": "selective_highlights",
        "pacing": "Fast opening, slower complete encounters.",
        "beats": [{
            "start_index": 0,
            "end_index": 1,
            "start_sec": 0.0,
            "end_sec": 20.0,
            "title": "Opening challenge",
            "description": "The premise is introduced.",
            "editorial_role": "setup",
            "confidence": 0.9,
        }],
        "notable_patterns": ["Narration supplies context before gameplay."],
        "uncertainties": [],
    }


class ReferenceAnalysisTests(unittest.TestCase):
    def test_long_transcript_excerpt_preserves_whole_video_coverage(self) -> None:
        rows = [
            {"start_ms": index * 1000, "end_ms": (index + 1) * 1000, "text": f"utterance-{index:03d}"}
            for index in range(120)
        ]

        excerpt = _transcript_excerpt(rows, limit=1200)

        self.assertIn("transcript coverage band 1/12", excerpt)
        self.assertIn("transcript coverage band 12/12", excerpt)
        self.assertIn("utterance-000", excerpt)
        self.assertIn("utterance-110", excerpt)

    def test_visual_learning_context_samples_entire_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "visual-learning.json"
            path.write_text(
                json.dumps({
                    "cache_identity": "visual-id",
                    "result": {
                        "segments": [
                            {
                                "start_ms": index * 1000,
                                "end_ms": (index + 1) * 1000,
                                "visual_category": "gameplay",
                                "description": f"event-{index:03d}",
                            }
                            for index in range(600)
                        ],
                        "frame_differences": [],
                    },
                }),
                encoding="utf-8",
            )

            context, identity = _visual_learning_context(path)

            self.assertEqual(identity, "visual-id")
            self.assertIn("event-000", context)
            self.assertIn("event-599", context)
            self.assertNotIn("event-001", context)

    def artifact(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        frame = root / "frame.jpg"
        frame.write_bytes(b"jpeg")
        artifact = root / "preprocessing.json"
        artifact.write_text(json.dumps({
            "video_id": "demo",
            "probe": {"duration_ms": 20000},
            "transcript": [{"start_ms": 0, "end_ms": 1000, "text": "hello"}],
            "samples": [
                {"timestamp_sec": 0.0, "jpeg_path": str(frame)},
                {"timestamp_sec": 20.0, "jpeg_path": str(frame)},
            ],
        }), encoding="utf-8")
        return artifact

    @patch("tools.reference_analysis.require_api_key", return_value="secret")
    @patch("tools.reference_analysis.request_json")
    def test_request_is_schema_enforced_and_checkpointed(self, request, _key) -> None:
        request.return_value = {"usage": {"input_tokens": 10, "output_tokens": 5}, "output": [{"content": [{"type": "output_text", "text": json.dumps(_answer())}]}]}
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            result = analyze_video(self.artifact(root / "input"), root / "output")
            self.assertEqual(result["status"], "complete")
            self.assertTrue((root / "output" / "demo" / "reference-analysis.request.json").is_file())
            self.assertTrue((root / "output" / "demo" / "reference-analysis.json").is_file())
            payload = request.call_args.args[2]
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertTrue(payload["text"]["format"]["strict"])
            self.assertEqual(payload["text"]["format"]["schema"]["additionalProperties"], False)

    @patch("tools.reference_analysis.request_json")
    @patch("tools.reference_analysis.require_api_key", return_value="secret")
    def test_complete_checkpoint_avoids_second_paid_request(self, _key, request) -> None:
        request.return_value = {"usage": {}, "output": [{"content": [{"type": "output_text", "text": json.dumps(_answer())}]}]}
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            artifact = self.artifact(root / "input")
            first = analyze_video(artifact, root / "output")
            second = analyze_video(artifact, root / "output")
            self.assertEqual(first, second)
            request.assert_called_once()

    @patch("tools.reference_analysis.analyze_video")
    def test_worker_cap_and_filter(self, analyze) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for video_id in ("a", "b", "c"):
                folder = root / "artifacts" / video_id
                folder.mkdir(parents=True)
                (folder / "preprocessing.json").write_text("{}", encoding="utf-8")
            run(root / "artifacts", root / "out", workers=99, only={"b"})
            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(analyze.call_args.args[0].parent.name, "b")

    @patch("tools.reference_analysis.analyze_video")
    def test_manifest_curator_context_is_forwarded(self, analyze) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            folder = root / "artifacts" / "a"
            folder.mkdir(parents=True)
            (folder / "preprocessing.json").write_text("{}", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"items": [{"key": "a", "notes": "Cold open; no narration."}]}),
                encoding="utf-8",
            )
            run(root / "artifacts", root / "out", manifest=manifest)
            self.assertEqual(
                analyze.call_args.kwargs["curator_context"]["notes"],
                "Cold open; no narration.",
            )


if __name__ == "__main__":
    unittest.main()
