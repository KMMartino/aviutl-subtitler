from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from subtitler.media_analysis import (
    AnalyzedRange,
    BoundaryClassification,
    MediaAnalysisResponseError,
    OpenAIMediaAnalysisProvider,
    VisualSample,
    _extract_sample,
    _extract_samples,
    _sample_timestamps,
    _sampling_plan,
    _transition_budget,
    analyze_media,
    compare_visual_samples,
)


class FakeProvider:
    provider = "openai"
    model = "gpt-5.6-terra"

    def analyze(self, samples, _media_kind, _title, _max_ranges):
        ranges = [
            AnalyzedRange(
                start_index=0,
                end_index=max(0, len(samples) - 2),
                description="Continuous gameplay",
                tags=["gameplay"],
                confidence=.9,
                motion_level=.5,
                visual_category="gameplay",
                suitability="Useful as illustrative B-roll",
            ),
            AnalyzedRange(
                start_index=len(samples) - 1,
                end_index=len(samples) - 1,
                description="Presenter appears",
                tags=["talking head"],
                confidence=.9,
                motion_level=.1,
                visual_category="talking_head",
                suitability="Avoid when selecting clean gameplay",
            ),
        ]
        return "Overall gameplay", ["gameplay"], ranges, 100, 50


class AdaptiveProvider:
    provider = "openai"
    model = "gpt-5.6-terra"

    def __init__(self, continuous: bool = False, multiple_cuts: bool = False) -> None:
        self.continuous = continuous
        self.multiple_cuts = multiple_cuts
        self.refinement_calls = 0
        self.max_ranges = 0

    def analyze(self, samples, _media_kind, _title, max_ranges):
        self.max_ranges = max_ranges
        if self.continuous:
            ranges = [AnalyzedRange(0, len(samples) - 1, "Continuous gameplay", ["gameplay"], .95, .5, "gameplay", "General gameplay B-roll")]
        else:
            midpoint = len(samples) // 2
            ranges = [
                AnalyzedRange(0, midpoint - 1, "Presenter discussion", ["talking head"], .95, .1, "talking_head", "Explanatory context"),
                AnalyzedRange(midpoint, len(samples) - 1, "Game trailer", ["trailer"], .95, .8, "trailer", "Trailer B-roll"),
            ]
        return "Long-form source", ["stream"], ranges, 1000, 200

    def refine_boundaries(self, probes):
        self.refinement_calls += 1
        decisions = {}
        for probe in probes:
            if self.multiple_cuts:
                category = (
                    "talking_head" if probe.timestamp_sec < 2950
                    else "cinematic" if probe.timestamp_sec < 2980
                    else "trailer"
                )
                if category == probe.left_category:
                    classification = BoundaryClassification("left")
                elif category == probe.right_category:
                    classification = BoundaryClassification("right")
                else:
                    classification = BoundaryClassification(
                        "new",
                        scene_id="cinematic",
                        description="Intermediate cinematic reveal",
                        tags=("cinematic",),
                        confidence=.9,
                        motion_level=.7,
                        visual_category="cinematic",
                        suitability="Dramatic reveal B-roll",
                    )
            else:
                classification = BoundaryClassification(
                    "left" if probe.timestamp_sec < 2980 else "right",
                )
            decisions[(probe.boundary_index, probe.probe_position)] = classification
        return decisions, 100, 20


class MediaAnalysisTests(unittest.TestCase):
    def test_openai_media_analysis_uses_strict_schema_and_records_raw_response(self) -> None:
        response_text = json.dumps(
            {
                "description": "Gameplay",
                "tags": ["gameplay"],
                "ranges": [
                    {
                        "start_index": 0,
                        "end_index": 0,
                        "description": "Exploration",
                        "observed_label": "Exploring the first floor",
                        "tags": ["exploration"],
                        "confidence": 0.9,
                        "motion_level": 0.4,
                        "visual_category": "gameplay",
                        "suitability": "Keepable gameplay",
                        "handoff_required": True,
                        "handoff_reason": "Outcome is not visible in the sampled still",
                    }
                ],
            }
        )
        response = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": response_text}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / "frame.jpg"
            frame.write_bytes(b"jpeg")
            diagnostics = root / "responses.jsonl"
            with patch("subtitler.media_analysis.request_json", return_value=response) as request:
                provider = OpenAIMediaAnalysisProvider(
                    "gpt-5.6-luna",
                    api_key="key",
                    diagnostics_path=diagnostics,
                )
                result = provider.analyze(
                    [VisualSample(0, 0.0, frame)], "video", "game", 1
                )

            payload = request.call_args.args[2]
            record = json.loads(diagnostics.read_text(encoding="utf-8"))

        self.assertEqual(result[0], "Gameplay")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(payload["max_output_tokens"], 16_384)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["response_content"], response_text)
        self.assertTrue(result[2][0].handoff_required)
        self.assertEqual(result[2][0].observed_label, "Exploring the first floor")

    def test_openai_media_analysis_reports_incomplete_structured_output(self) -> None:
        response = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
            "usage": {"input_tokens": 10, "output_tokens": 16_384},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / "frame.jpg"
            frame.write_bytes(b"jpeg")
            diagnostics = root / "responses.jsonl"
            with patch("subtitler.media_analysis.request_json", return_value=response):
                provider = OpenAIMediaAnalysisProvider(
                    "gpt-5.6-luna",
                    api_key="key",
                    diagnostics_path=diagnostics,
                )
                with self.assertRaises(MediaAnalysisResponseError) as raised:
                    provider.analyze(
                        [VisualSample(0, 0.0, frame)], "video", "game", 1
                    )
            record = json.loads(diagnostics.read_text(encoding="utf-8"))

        self.assertIn("max_output_tokens", str(raised.exception))
        self.assertEqual(record["incomplete_details"]["reason"], "max_output_tokens")

    def test_frame_extraction_converts_limited_range_for_jpeg(self) -> None:
        def run_ffmpeg(command, **_kwargs):
            video_filter = command[command.index("-vf") + 1]
            if "out_range=full" not in video_filter or "format=yuvj420p" not in video_filter:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="Non full-range YUV is non-standard",
                )
            Path(command[-1]).write_bytes(b"jpeg")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_name, patch(
            "subtitler.media_analysis.subprocess.run", side_effect=run_ffmpeg
        ):
            sample = _extract_sample(
                Path(temp_name) / "limited-range.mp4",
                media_kind="video",
                index=2,
                timestamp=1333.9,
                ffmpeg="ffmpeg",
                output_dir=Path(temp_name),
                prefix="sample",
            )

        self.assertEqual(sample.index, 2)
        self.assertEqual(sample.timestamp_sec, 1333.9)

    def test_frame_comparison_surfaces_the_strongest_adjacent_change(self) -> None:
        samples = [VisualSample(index, index * 10.0, Path(f"{index}.jpg")) for index in range(4)]
        frames = [
            np.zeros(64 * 36, dtype=np.uint8),
            np.full(64 * 36, 3, dtype=np.uint8),
            np.full(64 * 36, 250, dtype=np.uint8),
            np.full(64 * 36, 248, dtype=np.uint8),
        ]
        with patch("subtitler.media_analysis._sample_luma", side_effect=frames):
            evidence = compare_visual_samples(samples, ffmpeg="ffmpeg", output_dir=Path("."))
        self.assertEqual(max(evidence, key=lambda item: item["change_score"])["timestamp_ms"], 15_000)

    def test_frame_extraction_runs_concurrently_but_returns_timeline_order(self) -> None:
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def extract(_path, *, index, timestamp, output_dir, **_kwargs):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.015 * (4 - index))
            with lock:
                active -= 1
            return VisualSample(index, timestamp, output_dir / f"{index}.jpg")

        with tempfile.TemporaryDirectory() as temp_name, patch(
            "subtitler.media_analysis._extract_sample", side_effect=extract
        ):
            result = _extract_samples(
                Path(temp_name) / "video.mp4",
                media_kind="video",
                timestamps=[0.0, 1.0, 2.0, 3.0],
                ffmpeg="ffmpeg",
                output_dir=Path(temp_name),
                prefix="sample",
            )

        self.assertGreater(maximum_active, 1)
        self.assertEqual([item.index for item in result], [0, 1, 2, 3])

    def test_video_sampling_curves_match_anchors_and_probe_long_streams(self) -> None:
        self.assertEqual(
            [len(_sample_timestamps(2.0, detail)) for detail in ("simple", "medium", "detailed", "precise")],
            [1, 2, 5, 10],
        )
        self.assertEqual(
            [len(_sample_timestamps(120.0, detail)) for detail in ("simple", "medium", "detailed", "precise")],
            [10, 20, 50, 100],
        )
        self.assertEqual(
            [len(_sample_timestamps(1200.0, detail)) for detail in ("simple", "medium", "detailed", "precise")],
            [40, 80, 200, 400],
        )
        self.assertEqual(
            [len(_sample_timestamps(2400.0, detail)) for detail in ("simple", "medium", "detailed", "precise")],
            [80, 160, 400, 800],
        )
        for detail in ("simple", "medium", "detailed", "precise"):
            counts = [len(_sample_timestamps(duration, detail)) for duration in (2.0, 10.0, 30.0, 60.0, 120.0, 1200.0)]
            self.assertEqual(counts, sorted(counts))
        self.assertEqual(len(_sample_timestamps(1200.0, "probe")), 40)
        self.assertEqual(len(_sample_timestamps(6000.0, "probe")), 89)
        self.assertTrue(_sampling_plan(6000.0, "probe").adaptive)
        self.assertFalse(_sampling_plan(6000.0, "precise").adaptive)
        self.assertEqual(_sampling_plan(6000.0, "probe").breakpoint_precision_sec, 3.0)
        self.assertEqual(_transition_budget(6000.0), 40)
        self.assertEqual(_transition_budget(10_800.0), 72)
        self.assertEqual(_transition_budget(20_000.0), 96)

    def test_editorial_sampling_scale_adds_bounded_coarse_evidence(self) -> None:
        self.assertEqual(len(_sample_timestamps(3600.0, "simple")), 120)
        self.assertEqual(len(_sample_timestamps(3600.0, "simple", 1.5)), 180)
        self.assertEqual(_sampling_plan(6000.0, "probe", 1.5).coarse_count, 134)

    def test_analysis_builds_content_bounded_semantic_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            media = Path(temp_name) / "gameplay.mp4"
            media.write_bytes(b"fixture")
            samples = [
                VisualSample(0, 0.0, Path(temp_name) / "a.jpg"),
                VisualSample(1, 10.0, Path(temp_name) / "b.jpg"),
                VisualSample(2, 20.0, Path(temp_name) / "c.jpg"),
            ]
            with patch("subtitler.media_analysis.sample_media", return_value=samples):
                result = analyze_media(
                    media_path=media,
                    media_kind="video",
                    duration_sec=30.0,
                    ffmpeg="ffmpeg",
                    provider=FakeProvider(),
                )
        self.assertEqual(result.description, "Overall gameplay")
        self.assertEqual(
            [(item.start_ms, item.end_ms, item.visual_category) for item in result.segments],
            [(0, 15000, "gameplay"), (15000, 30000, "talking_head")],
        )
        self.assertGreater(result.cost_usd, 0)

    def test_range_analysis_keeps_returned_segments_on_the_absolute_source_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            media = Path(temp_name) / "gameplay.mp4"
            media.write_bytes(b"fixture")
            samples = [
                VisualSample(0, 25.0, Path(temp_name) / "a.jpg"),
                VisualSample(1, 50.0, Path(temp_name) / "b.jpg"),
                VisualSample(2, 75.0, Path(temp_name) / "c.jpg"),
            ]
            with patch("subtitler.media_analysis.sample_media", return_value=samples):
                result = analyze_media(
                    media_path=media,
                    media_kind="video",
                    duration_sec=120.0,
                    start_sec=25.0,
                    end_sec=100.0,
                    ffmpeg="ffmpeg",
                    provider=FakeProvider(),
                )
        self.assertEqual(
            [(item.start_ms, item.end_ms) for item in result.segments],
            [(25_000, 62_500), (62_500, 100_000)],
        )

    def test_long_video_refines_only_meaningful_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            media = Path(temp_name) / "stream.mp4"
            media.write_bytes(b"fixture")
            coarse_count = _sampling_plan(6000.0, "probe").coarse_count
            coarse = [
                VisualSample(index, 5999.9 * index / (coarse_count - 1), Path(temp_name) / f"coarse-{index}.jpg")
                for index in range(coarse_count)
            ]

            def extracted(_media_path, *, timestamps, **_kwargs):
                return [
                    VisualSample(index, timestamp, Path(temp_name) / f"refine-{index}.jpg")
                    for index, timestamp in enumerate(timestamps)
                ]

            provider = AdaptiveProvider()
            with (
                patch("subtitler.media_analysis.sample_media", return_value=coarse),
                patch("subtitler.media_analysis._extract_samples", side_effect=extracted),
            ):
                result = analyze_media(
                    media_path=media,
                    media_kind="video",
                    duration_sec=6000.0,
                    detail="probe",
                    ffmpeg="ffmpeg",
                    provider=provider,
                )
        self.assertEqual(provider.refinement_calls, 3)
        self.assertEqual(provider.max_ranges, 41)
        self.assertEqual(result.sample_count, 95)
        self.assertLessEqual(abs(result.segments[0].end_ms / 1000 - 2980), 3)
        self.assertEqual([segment.visual_category for segment in result.segments], ["talking_head", "trailer"])

    def test_long_video_discovers_multiple_cuts_inside_one_coarse_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            media = Path(temp_name) / "stream.mp4"
            media.write_bytes(b"fixture")
            coarse_count = _sampling_plan(6000.0, "probe").coarse_count
            coarse = [
                VisualSample(index, 5999.9 * index / (coarse_count - 1), Path(temp_name) / f"coarse-{index}.jpg")
                for index in range(coarse_count)
            ]

            def extracted(_media_path, *, timestamps, **_kwargs):
                return [
                    VisualSample(index, timestamp, Path(temp_name) / f"refine-{index}.jpg")
                    for index, timestamp in enumerate(timestamps)
                ]

            provider = AdaptiveProvider(multiple_cuts=True)
            with (
                patch("subtitler.media_analysis.sample_media", return_value=coarse),
                patch("subtitler.media_analysis._extract_samples", side_effect=extracted),
            ):
                result = analyze_media(
                    media_path=media,
                    media_kind="video",
                    duration_sec=6000.0,
                    detail="probe",
                    ffmpeg="ffmpeg",
                    provider=provider,
                )
        self.assertEqual(provider.refinement_calls, 3)
        self.assertEqual(result.sample_count, 99)
        self.assertEqual(
            [segment.visual_category for segment in result.segments],
            ["talking_head", "cinematic", "trailer"],
        )
        self.assertLessEqual(abs(result.segments[0].end_ms / 1000 - 2950), 3)
        self.assertLessEqual(abs(result.segments[1].end_ms / 1000 - 2980), 3)

    def test_continuous_long_video_needs_no_refinement_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            media = Path(temp_name) / "gameplay.mp4"
            media.write_bytes(b"fixture")
            coarse_count = _sampling_plan(6000.0, "probe").coarse_count
            coarse = [
                VisualSample(index, 5999.9 * index / (coarse_count - 1), Path(temp_name) / f"coarse-{index}.jpg")
                for index in range(coarse_count)
            ]
            provider = AdaptiveProvider(continuous=True)
            with (
                patch("subtitler.media_analysis.sample_media", return_value=coarse),
                patch("subtitler.media_analysis._extract_samples") as extract,
            ):
                result = analyze_media(
                    media_path=media,
                    media_kind="video",
                    duration_sec=6000.0,
                    detail="probe",
                    ffmpeg="ffmpeg",
                    provider=provider,
                )
        extract.assert_not_called()
        self.assertEqual(provider.refinement_calls, 0)
        self.assertEqual(result.sample_count, 89)
        self.assertEqual(len(result.segments), 1)


if __name__ == "__main__":
    unittest.main()
