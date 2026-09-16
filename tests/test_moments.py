import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from subtitler.errors import SubtitlerError
from subtitler.media_layout import VideoGeometry
from subtitler.media_analysis import AnalysisSegment, MediaAnalysisResult
from subtitler.evidence import TranscriptEvidence
from subtitler.moment_export import export_moments, write_moment_report
from subtitler.moment_selection import merge_matches, select_moments
from subtitler.moments_cli import prepare_range, run_moments
from subtitler.source_inspection import SourceInspection


def evidence(key, start, end, kind="speech"):
    return {"id": key, "start_ms": start, "end_ms": end, "kind": kind, "text": key}


def match(start=1000, end=3000, refs=None, detours=None):
    return {"start_ms": start, "end_ms": end, "explanation": "Requested topic", "confidence": "medium",
            "relevance": "high", "evidence_ids": refs or ["a"], "detours": detours or []}


class MomentSelectionTests(unittest.TestCase):
    def test_scans_every_window_and_keeps_separated_returns_to_topic(self):
        rows = [evidence("a", 1000, 3000), evidence("detour", 8000, 9000), evidence("b", 190000, 192000)]
        provider = Mock()
        provider.complete_structured.side_effect = [json.dumps({"matches": [{
            "first_id": key, "last_id": key, "explanation": key, "confidence": "low", "relevance": "high",
            "evidence_ids": [key], "detours": [],
        }]}) for key in ["a", "b"]]
        result = select_moments(provider, "topic", rows, 250000)
        self.assertEqual([(r["start_ms"], r["end_ms"]) for r in result], [(1000, 3000), (190000, 192000)])
        self.assertEqual(provider.complete_structured.call_count, 2)

    def test_pure_visual_matches_and_no_matches(self):
        rows = [evidence("v", 0, 5000, "visual")]
        provider = Mock()
        provider.complete_structured.return_value = json.dumps({"matches": []})
        self.assertEqual(select_moments(provider, "absent", rows, 5000), [])
        provider.complete_structured.return_value = json.dumps({"matches": [{
            "first_id": "v", "last_id": "v", "explanation": "visible", "confidence": "high", "relevance": "high",
            "evidence_ids": ["v"], "detours": [],
        }]})
        self.assertEqual(select_moments(provider, "visual", rows, 5000)[0]["end_ms"], 5000)

    def test_hallucinated_references_and_outside_detours_fail(self):
        rows = [evidence("a", 1000, 3000), evidence("b", 5000, 6000)]
        for detours, refs in [([], ["invented"]), ([{"first_id": "b", "last_id": "b", "explanation": "detour"}], ["a"])]:
            provider = Mock()
            provider.complete_structured.return_value = json.dumps({"matches": [{
                "first_id": "a", "last_id": "a", "explanation": "topic", "confidence": "high", "relevance": "high",
                "evidence_ids": refs, "detours": detours,
            }]})
            with self.assertRaises(SubtitlerError):
                select_moments(provider, "topic", rows, 7000)

    def test_overlap_deduplication_expands_speech_and_preserves_manual_detours(self):
        detour = {"start_ms": 2500, "end_ms": 2800, "explanation": "Intertwined topic"}
        result = merge_matches([match(1000, 3000, detours=[detour]), match(2000, 3500), match(6000, 7000)],
                               [evidence("a", 500, 4000)], 8000)
        self.assertEqual([(r["start_ms"], r["end_ms"]) for r in result], [(500, 4000), (6000, 7000)])
        self.assertEqual(result[0]["detours"], [detour])


class MomentExportTests(unittest.TestCase):
    def test_estimate_only_rejects_before_media_processing_or_hosted_calls(self):
        with patch("subtitler.moments_cli.load_workflow_config", return_value={"cost": {"estimate_cost_only": True}}), \
             patch("subtitler.moments_cli.inspect_recording") as inspect, \
             patch("subtitler.moments_cli._analyze_editorial_visual_windows") as vision, \
             patch("subtitler.moments_cli.build_refiner") as provider:
            with self.assertRaisesRegex(SubtitlerError, "does not support estimate-only"):
                run_moments({"query": "topic", "sourcePath": "silent.mp4"}, Path("unused"), Path("unused"), Path("unused"))
            inspect.assert_not_called()
            vision.assert_not_called()
            provider.assert_not_called()

    def test_paired_media_explanations_and_detours_share_distinct_excerpt_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            geometry = VideoGeometry(1920, 1080, 30)
            inspection = SourceInspection(10000, 10000, 30, geometry, geometry, None)
            artifact = {"matches": [match(detours=[{"start_ms": 1500, "end_ms": 2000, "explanation": "Manual"}]), match(5000, 6000)], "origin_offset_ms": 60000}
            media = [{"path": root / "game.mp4", "fps": 30, "has_audio": True},
                     {"path": root / "face.mp4", "fps": 24, "has_audio": True}]
            path = root / "moments.exo"
            export_moments(path, artifact, inspection, media, fps=60)
            text = path.read_text(encoding="cp932")
            headers = re.findall(r"\[\d+\]\nstart=(\d+)\nend=(\d+)\nlayer=(\d+)\ngroup=(\d+)", text)
            self.assertGreaterEqual(len(headers), 11)
            for group in ("1", "2"):
                self.assertTrue({1, 2, 3, 4, 5}.issubset({int(h[2]) for h in headers if h[3] == group}))
            self.assertIn("再生位置=31", text)
            self.assertIn("再生位置=25", text)
            self.assertEqual(artifact["matches"][1]["timeline_start_frame"], 121)
            self.assertEqual(artifact["matches"][1]["timeline_end_frame"], 180)

    def test_report_escapes_content_and_uses_original_vod_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.html"
            artifact = {"query": "<script>alert(1)</script>", "matches": [{"id": "one", **match()}],
                        "evidence": [evidence("a", 1000, 3000)], "origin_offset_ms": 60000,
                        "range_start_ms": 0, "range_end_ms": 10000, "api_cost_usd": .1}
            write_moment_report(path, artifact)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<script>", text)
            self.assertIn("00:01:01.000", text)

    def test_exact_range_preparation_and_rejection_before_paid_calls(self):
        with patch("subtitler.moments_cli.subprocess.run") as run, patch("subtitler.moments_cli.probe_media", return_value={"duration_sec": 10}):
            prepare_range(Path("video.mp4"), Path("range.mkv"), 15, 25)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-ss") + 1], "15")
            self.assertEqual(command[command.index("-t") + 1], "10")
            self.assertNotIn("copy", command)
        with patch("subtitler.moments_cli.build_refiner") as provider:
            with self.assertRaises(SubtitlerError):
                run_moments({"query": "topic", "sourcePath": "missing-moment-source.mp4"}, Path("unused"), Path("unused"), Path("unused"))
            provider.assert_not_called()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg integration requires installed tools")
class MomentPipelineTests(unittest.TestCase):
    def test_bounded_pipeline_exports_original_offsets_with_mocked_hosted_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "recording.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=4",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac",
                            "-shortest", str(source)], check=True)
            provider = Mock()
            provider.complete_structured.return_value = json.dumps({"matches": [{
                "first_id": "speech-0", "last_id": "speech-0", "explanation": "The requested discussion",
                "confidence": "medium", "relevance": "high", "evidence_ids": ["speech-0"], "detours": [],
            }]})
            visual = MediaAnalysisResult("Blue scene", [], [AnalysisSegment(0, 2000, "Blue scene", [], .8, None, "other", "usable")],
                                         "openai", "test", "test", 2, 10, 20, .03)
            with patch("subtitler.moments_cli.require_api_key", return_value="test"), \
                 patch("subtitler.moments_cli.run_transcript_workflow", return_value=SimpleNamespace(api_cost_usd=.02, document_path=root / "transcript.json")) as transcription, \
                 patch("subtitler.moments_cli.load_transcript_evidence", return_value=[TranscriptEvidence(0, 2000, "Requested discussion")]), \
                 patch("subtitler.moments_cli._analyze_editorial_visual_windows", return_value=visual) as vision, \
                 patch("subtitler.moments_cli.build_refiner", return_value=provider):
                run_moments({"sourcePath": str(source), "query": "discussion", "startSec": 1, "endSec": 3,
                             "originOffsetSec": 60}, Path("configs/hosted-long-stream.json"), root / ".env", root / "result.json")
            self.assertEqual(transcription.call_args.kwargs["source_path"].name, "range-0.mkv")
            self.assertEqual(vision.call_args.kwargs["duration_sec"], 2)
            artifact = json.loads((root / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "complete")
            self.assertEqual((artifact["matches"][0]["start_ms"], artifact["matches"][0]["end_ms"]), (1000, 3000))
            self.assertIn("boundary_warning", artifact["matches"][0])
            self.assertAlmostEqual(artifact["api_cost_usd"], .05)
            self.assertIn("00:01:01.000", (root / "result.html").read_text(encoding="utf-8"))
            self.assertIn("再生位置=31", (root / "result.exo").read_text(encoding="cp932"))


if __name__ == "__main__":
    unittest.main()
