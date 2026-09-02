import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.media_analysis import VisualSample
from tools.research_harness import load_manifest, parse_vtt, process_video, run, transcript_artifacts


class ResearchHarnessTests(unittest.TestCase):
    def test_parse_youtube_vtt_and_emit_app_artifacts(self):
        evidence = parse_vtt(
            "WEBVTT\n\n00:00:01.200 --> 00:00:02.500\n<c>hello</c> &amp; world\n\n"
        )
        self.assertEqual(evidence[0].start_ms, 1200)
        self.assertEqual(evidence[0].text, "hello & world")
        timing, text = transcript_artifacts(evidence)
        self.assertIn("1.2,2.5", timing)
        self.assertEqual(text, "1. hello & world\n")

    def test_parse_youtube_rolling_captions_without_repeated_previous_line(self):
        evidence = parse_vtt(
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "Hello<00:00:00.500><c> there</c>\n\n"
            "00:00:02.000 --> 00:00:02.010\nHello there\n\n"
            "00:00:02.010 --> 00:00:04.000\n"
            "Hello there\nGeneral<00:00:02.500><c> Kenobi</c>\n"
        )
        self.assertEqual([item.text for item in evidence], ["Hello there", "General Kenobi"])

    def test_process_uses_app_media_pipeline_and_writes_atomic_checkpoint(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source"
            source.mkdir()
            media = source / "clip.mp4"
            media.write_bytes(b"video")
            (source / "clip.en-orig.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n", encoding="utf-8"
            )
            sample = VisualSample(0, 0.0, root / "sample.jpg")
            sample.jpeg_path.write_bytes(b"jpg")
            with patch("tools.research_harness.get_media_duration", return_value=1.0), \
                patch("tools.research_harness.probe_video_geometry", return_value=type("G", (), {"width": 2, "height": 2, "frame_rate": 30.0})()), \
                patch("tools.research_harness.sample_media", return_value=[sample]) as sampled, \
                patch("tools.research_harness.compare_visual_samples", return_value=[]) as compared:
                result = process_video({"id": "one", "source_folder": str(source)}, output_root=root / "out")
            sampled.assert_called_once()
            compared.assert_called_once()
            saved = json.loads((root / "out" / "one" / "preprocessing.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(result["transcript"][0]["text"], "hello")

    def test_process_reuses_matching_complete_checkpoint(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source"
            source.mkdir()
            media = source / "clip.mp4"
            media.write_bytes(b"video")
            frame = root / "out" / "one" / "frames" / "sample.jpg"
            frame.parent.mkdir(parents=True)
            frame.write_bytes(b"jpg")
            media_stat = media.stat()
            expected = {
                "schema_version": 1,
                "status": "complete",
                "source_fingerprint": {
                    "media_path": str(media.resolve()),
                    "media_size": media_stat.st_size,
                    "media_mtime_ns": media_stat.st_mtime_ns,
                },
                "samples": [{"jpeg_path": str(frame)}],
            }
            (frame.parent.parent / "preprocessing.json").write_text(
                json.dumps(expected), encoding="utf-8"
            )
            with patch("tools.research_harness.sample_media") as sampled:
                result = process_video(
                    {"id": "one", "source_folder": str(source)}, output_root=root / "out"
                )
            sampled.assert_not_called()
            self.assertEqual(result, expected)

    def test_manifest_accepts_wrapped_and_bare_lists(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "manifest.json"
            path.write_text(json.dumps({"videos": [{"id": "a"}]}), encoding="utf-8")
            self.assertEqual(load_manifest(path)[0]["id"], "a")

    def test_run_resolves_manifest_items_to_youtube_source_folders(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"items": [{
                "key": "finished-a", "kind": "finished",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
            }]}), encoding="utf-8")
            with patch("tools.research_harness.process_video", return_value={}) as process:
                run(manifest, root / "out", source_root=root / "sources")
            video = process.call_args.args[0]
            self.assertEqual(video["id"], "finished-a")
            self.assertEqual(video["source_folder"], str(root / "sources" / "abcdefghijk"))
            self.assertEqual(video["detail"], "precise")


if __name__ == "__main__":
    unittest.main()
