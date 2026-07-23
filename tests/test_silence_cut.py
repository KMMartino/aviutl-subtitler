import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from subtitler.errors import SubtitlerError
from subtitler.models import ExoMarker, Subtitle
from subtitler.silence_cut import (
    FRONTEND_EVENT_PREFIX,
    MARK_AND_REJECT_TEXT,
    TimelineMap,
    MediaStreamSummary,
    build_exo_media_plan,
    build_cut_candidates,
    build_filter_script,
    execute_silence_cut,
    encode_cut_video,
    merge_cut_ranges,
    quantize_cuts_to_source_frames,
    request_review,
)
from subtitler.transcription_backend import RawVadSpeechInterval


class SilenceCandidateTests(unittest.TestCase):
    def test_five_second_gap_proposes_four_point_three_second_cut(self) -> None:
        candidates = build_cut_candidates([RawVadSpeechInterval(1.0, 2.0), RawVadSpeechInterval(7.0, 8.0)])
        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0].cut_start, 2.5)
        self.assertAlmostEqual(candidates[0].cut_end, 6.8)
        self.assertAlmostEqual(candidates[0].cut_duration, 4.3)

    def test_edges_and_safety_consumed_gaps_are_not_candidates(self) -> None:
        self.assertEqual(build_cut_candidates([]), [])
        self.assertEqual(build_cut_candidates([RawVadSpeechInterval(10.0, 11.0)]), [])
        self.assertEqual(
            build_cut_candidates([RawVadSpeechInterval(1.0, 2.0), RawVadSpeechInterval(2.6, 3.0)]),
            [],
        )

    def test_proposed_cuts_shorter_than_half_a_second_are_not_candidates(self) -> None:
        self.assertEqual(
            build_cut_candidates([RawVadSpeechInterval(1.0, 2.0), RawVadSpeechInterval(3.19, 4.0)]),
            [],
        )
        candidates = build_cut_candidates(
            [RawVadSpeechInterval(1.0, 2.0), RawVadSpeechInterval(3.2, 4.0)]
        )
        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0].cut_duration, 0.5)

    def test_overlapping_speech_is_merged_before_gap_analysis(self) -> None:
        candidates = build_cut_candidates(
            [
                RawVadSpeechInterval(0.0, 2.0),
                RawVadSpeechInterval(1.5, 3.0),
                RawVadSpeechInterval(8.0, 9.0),
            ]
        )
        self.assertEqual((candidates[0].silence_start, candidates[0].silence_end), (3.0, 8.0))


class SilenceTimelineTests(unittest.TestCase):
    def test_ranges_merge_and_time_mapping_clamps_removed_points(self) -> None:
        cuts = merge_cut_ranges([(2.0, 4.0), (4.0, 5.0), (8.0, 9.0)])
        self.assertEqual(cuts, [(2.0, 5.0), (8.0, 9.0)])
        timeline = TimelineMap(cuts)
        self.assertEqual(timeline.map_time(3.0), 2.0)
        self.assertEqual(timeline.map_time(5.0), 2.0)
        self.assertEqual(timeline.map_time(10.0), 6.0)

    def test_filter_script_keeps_primary_video_and_every_audio_track(self) -> None:
        script = build_filter_script(10.0, [(2.5, 6.8)], 2)
        self.assertIn("[0:v:0]trim", script)
        self.assertIn("[0:a:0]atrim", script)
        self.assertIn("[0:a:1]atrim", script)
        self.assertIn("[a0out]", script)
        self.assertIn("[a1out]", script)

    def test_frame_quantization_shrinks_cuts_and_media_segments_are_gapless(self) -> None:
        cuts = quantize_cuts_to_source_frames([(1.001, 2.999)], Fraction(30, 1))
        self.assertEqual(cuts, [(31 / 30, 89 / 30)])
        plan = build_exo_media_plan(Path("source.mkv"), 4.0, cuts, Fraction(30, 1), 60)
        self.assertEqual([(item.output_start_frame, item.output_end_frame) for item in plan.segments], [(1, 62), (63, 124)])
        self.assertEqual([item.source_start_frame for item in plan.segments], [1, 90])

    def test_quantization_discards_subframe_cut(self) -> None:
        self.assertEqual(quantize_cuts_to_source_frames([(1.001, 1.02)], Fraction(30, 1)), [])

    def test_exo_source_mode_needs_no_encoder_and_does_not_encode(self) -> None:
        candidate = build_cut_candidates([RawVadSpeechInterval(0.0, 1.0), RawVadSpeechInterval(4.0, 5.0)])[0]
        summary = MediaStreamSummary(True, 1, 0, 0, 0, 0, False, Fraction(30), Fraction(30), Fraction(1, 1000), "reported-cfr")
        with patch("subtitler.silence_cut.probe_media_streams", return_value=summary), patch("subtitler.silence_cut.encode_cut_video") as encode:
            outcome = execute_silence_cut(
                mode="automatic", candidates=[candidate], raw_intervals=[RawVadSpeechInterval(0.0, 1.0), RawVadSpeechInterval(4.0, 5.0)],
                subtitles=[Subtitle(0.0, 1.0, "line")], chapter_markers=[], qa_markers=[], duration_sec=5.0,
                input_path=Path("source.mkv"), exo_path=Path("out.exo"), encoder_preset=None,
                frontend_protocol=None, render_cut_video=False, project_fps=60,
            )
        encode.assert_not_called()
        self.assertEqual(outcome.output_strategy, "exo-source")
        self.assertIsNotNone(outcome.media_plan)

    def test_rendered_mode_references_the_cut_mkv_as_one_segment(self) -> None:
        candidate = build_cut_candidates([RawVadSpeechInterval(0.0, 1.0), RawVadSpeechInterval(4.0, 5.0)])[0]
        summary = MediaStreamSummary(True, 2, 0, 0, 0, 0, False, Fraction(30), Fraction(30), Fraction(1, 1000), "reported-cfr")
        cut_path = Path("result.cut.mkv")
        with patch("subtitler.silence_cut.probe_media_streams", return_value=summary), patch(
            "subtitler.silence_cut.encode_cut_video", return_value=(cut_path, [])
        ) as encode:
            outcome = execute_silence_cut(
                mode="automatic", candidates=[candidate], raw_intervals=[RawVadSpeechInterval(0.0, 1.0), RawVadSpeechInterval(4.0, 5.0)],
                subtitles=[], chapter_markers=[], qa_markers=[], duration_sec=5.0,
                input_path=Path("source.mkv"), exo_path=Path("out.exo"), encoder_preset="libx265-crf21",
                frontend_protocol=None, render_cut_video=True, project_fps=60,
            )
        encode.assert_called_once_with(Path("source.mkv"), Path("out.exo"), 5.0, outcome.requested_cuts, "libx265-crf21", 60)
        self.assertEqual(outcome.output_strategy, "rendered-mkv")
        self.assertEqual(outcome.media_plan.source_path, cut_path.resolve())
        self.assertEqual(len(outcome.media_plan.segments), 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_generated_multi_audio_video_is_cut_and_retains_aac_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mkv"
            exo = root / "result.exo"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
                    "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-c:v", "libx264", "-c:a", "pcm_s16le", str(source),
                ],
                check=True,
            )
            output, omitted = encode_cut_video(source, exo, 3.0, [(1.0, 2.0)], "libx265-crf21")
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,avg_frame_rate", "-of", "json", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            value = json.loads(probe.stdout)
            self.assertLess(float(value["format"]["duration"]), 2.2)
            audio = [stream for stream in value["streams"] if stream["codec_type"] == "audio"]
            self.assertEqual([stream["codec_name"] for stream in audio], ["aac", "aac"])
            video = next(stream for stream in value["streams"] if stream["codec_type"] == "video")
            self.assertEqual(video["avg_frame_rate"], "60/1")
            self.assertEqual(omitted, [])


class SilenceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = build_cut_candidates(
            [RawVadSpeechInterval(1.0, 2.0), RawVadSpeechInterval(7.0, 8.0)]
        )[0]

    def test_review_requires_every_candidate(self) -> None:
        response = json.dumps({"type": "silence-review-result", "reviewId": "wrong", "decisions": []}) + "\n"
        with patch("sys.stdin", io.StringIO(response)), redirect_stdout(io.StringIO()), self.assertRaises(SubtitlerError):
            request_review([self.candidate], "stdio-v1")

    def test_mark_and_reject_keeps_timeline_and_adds_qa_marker(self) -> None:
        decision = {
            "type": "silence-review-result",
            "reviewId": "fixed-review",
            "decisions": [{"candidateId": self.candidate.id, "decision": "mark_and_reject"}],
        }
        with (
            patch("subtitler.silence_cut.uuid.uuid4", return_value="fixed-review"),
            patch("sys.stdin", io.StringIO(json.dumps(decision) + "\n")),
            redirect_stdout(io.StringIO()) as output,
        ):
            outcome = execute_silence_cut(
                mode="review",
                candidates=[self.candidate],
                raw_intervals=[RawVadSpeechInterval(1.0, 2.0), RawVadSpeechInterval(7.0, 8.0)],
                subtitles=[Subtitle(1.0, 2.0, "line")],
                chapter_markers=[],
                qa_markers=[],
                duration_sec=10.0,
                input_path=Path("unused.mp4"),
                exo_path=Path("unused.exo"),
                encoder_preset="libx265-crf21",
                frontend_protocol="stdio-v1",
            )
        self.assertTrue(output.getvalue().startswith(FRONTEND_EVENT_PREFIX))
        self.assertEqual(outcome.accepted_cuts, [])
        self.assertEqual(outcome.qa_markers, [ExoMarker(self.candidate.cut_start, self.candidate.cut_end, MARK_AND_REJECT_TEXT)])


if __name__ == "__main__":
    unittest.main()
