from __future__ import annotations

import os
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.editorial_project import EDITORIAL_STAGE_VERSIONS
from subtitler.errors import SubtitlerError
from subtitler.media_analysis import PROMPT_VERSION, AnalysisSegment, MediaAnalysisResult
from subtitler.visual_analysis import analyze_visual_windows
from subtitler.visual_evidence_store import load_visual_evidence, publish_visual_evidence


def analysis(start=0, end=720_000):
    return MediaAnalysisResult("Project-specific story", [], [AnalysisSegment(
        start, end, "A player dodges", ["dodge"], .9, .5, "gameplay", "story payoff",
        observed_label="Dodging an attack", evidence_spacing_sec=.5,
    )], "openai", "model", "test", 10, 100, 20, .1)


class SharedVisualTests(unittest.TestCase):
    def test_broll_uses_broad_section_budget_without_reducing_editorial_detail(self):
        for profile, duration, expected in [("broll", 52, 6), ("broll", 720, 12), ("editorial", 720, 36)]:
            with self.subTest(profile=profile, duration=duration):
                run = Mock(return_value=analysis(0, duration * 1000))
                analyze_visual_windows(media_path=Path("missing-test-video.mp4"), duration_sec=duration,
                    detail="detailed", ffmpeg="ffmpeg", sampling_scale=1, model="model", reasoning_effort="low",
                    output_locale="en", editorial_context="", profile=profile, provider_factory=Mock(), analyze=run)
                self.assertEqual(run.call_args.kwargs["max_ranges"], expected)
                self.assertEqual(run.call_args.kwargs["detail"], "detailed")
                self.assertEqual(run.call_args.kwargs["end_sec"], duration)

    def test_broll_library_cache_excludes_editorial_and_obsolete_fine_grained_results(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SUBUTL_VISUAL_CACHE": directory}):
            media = Path(directory) / "clip.mp4"
            media.write_bytes(b"clip")
            publish_visual_evidence(media, analysis(0, 10_000), profile="editorial",
                stage_version=EDITORIAL_STAGE_VERSIONS["visual_learning"], start_sec=0, end_sec=10)
            self.assertIsNone(load_visual_evidence(media, end_sec=10, profile="broll"))
            self.assertIsNotNone(load_visual_evidence(media, end_sec=10, profile="editorial"))
            publish_visual_evidence(media, analysis(0, 10_000), profile="broll", stage_version=1, start_sec=0, end_sec=10)
            self.assertIsNotNone(load_visual_evidence(media, end_sec=10, profile="broll"))
            for cache in Path(directory).rglob("broll-*.json"):
                data = json.loads(cache.read_text(encoding="utf-8"))
                data["prompt_version"] = PROMPT_VERSION
                cache.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(load_visual_evidence(media, end_sec=10, profile="broll"))
            self.assertIsNotNone(load_visual_evidence(media, end_sec=10, profile="editorial"))

    def test_interrupted_windows_resume_and_keep_observed_labels(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SUBUTL_VISUAL_CACHE": directory}):
            media = Path(directory) / "clip.mp4"
            media.write_bytes(b"first media")
            run = Mock(side_effect=[analysis(), SubtitlerError("interrupted")])
            arguments = dict(media_path=media, duration_sec=800, detail="detailed", ffmpeg="ffmpeg",
                sampling_scale=1, model="model", reasoning_effort="low", output_locale="en", editorial_context="",
                profile="broll", max_workers=1, provider_factory=Mock(), progress_path=Path(directory) / "progress.json")
            with self.assertRaises(SubtitlerError):
                analyze_visual_windows(**arguments, analyze=run)
            resumed = Mock(return_value=analysis(720_000, 800_000))
            result = analyze_visual_windows(**arguments, analyze=resumed)
            self.assertEqual(resumed.call_count, 1)
            self.assertEqual(resumed.call_args.kwargs["start_sec"], 720)
            self.assertEqual(result.segments[0].observed_label, "Dodging an attack")
            self.assertEqual(result.segments[0].evidence_spacing_sec, .5)
            media.write_bytes(b"different media")
            changed = Mock(side_effect=[analysis(), analysis(720_000, 800_000)])
            analyze_visual_windows(**arguments, analyze=changed)
            self.assertEqual(changed.call_count, 2)

    def test_editorial_observations_reuse_without_story_claims_or_new_cost(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SUBUTL_VISUAL_CACHE": directory}):
            media = Path(directory) / "clip.mp4"
            media.write_bytes(b"clip")
            publish_visual_evidence(media, analysis(0, 10_000), profile="editorial",
                stage_version=EDITORIAL_STAGE_VERSIONS["visual_learning"], start_sec=0, end_sec=10)
            reused = load_visual_evidence(media, start_sec=2, end_sec=4, maximum_spacing_sec=1)
            self.assertIsNotNone(reused)
            self.assertEqual(reused.cost_usd, 0)
            self.assertEqual(reused.description, "Dodging an attack")
            self.assertEqual(reused.segments[0].suitability, "")
            self.assertEqual((reused.segments[0].start_ms, reused.segments[0].end_ms), (2000, 4000))
            self.assertIsNone(load_visual_evidence(media, start_sec=2, end_sec=4, maximum_spacing_sec=.1))
            self.assertIsNone(load_visual_evidence(media, end_sec=12))
            self.assertIsNone(load_visual_evidence(media, end_sec=10, model="different-model"))
            publish_visual_evidence(media, analysis(0, 10_000), profile="editorial",
                stage_version=EDITORIAL_STAGE_VERSIONS["visual_learning"] - 1, start_sec=0, end_sec=10)
            self.assertIsNone(load_visual_evidence(media, end_sec=10))

    def test_gapped_or_uncertain_evidence_cannot_verify_a_clip(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SUBUTL_VISUAL_CACHE": directory}):
            media = Path(directory) / "clip.mp4"
            media.write_bytes(b"clip")
            for result in (analysis(1000, 10_000), replace(analysis(0, 10_000),
                    segments=[replace(analysis(0, 10_000).segments[0], handoff_required=True)])):
                publish_visual_evidence(media, result, profile="broll", stage_version=1, start_sec=0, end_sec=10)
                self.assertIsNone(load_visual_evidence(media, end_sec=10, maximum_spacing_sec=1))

    def test_cancellation_starts_no_provider_requests(self):
        provider = Mock()
        with self.assertRaisesRegex(SubtitlerError, "cancelled"):
            analyze_visual_windows(media_path=Path("clip.mp4"), duration_sec=10, detail="simple", ffmpeg="ffmpeg",
                sampling_scale=1, model="model", reasoning_effort="low", output_locale="en", editorial_context="",
                provider_factory=provider, cancelled=lambda: True)
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
