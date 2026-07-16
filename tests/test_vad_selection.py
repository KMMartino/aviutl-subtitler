import unittest

from subtitler.backends.existing_pipeline import CleanupGroupPolicy, _cleanup_group_max_sec, long_stream_default_duration_ratio
from subtitler.models import AudioChunk
from subtitler.vad import _speech_timestamps_from_probabilities, assign_vad_groups_by_largest_gaps, select_high_activation_chunks


def _chunk(index: int, activation: float, peak: float = 0.0) -> AudioChunk:
    return AudioChunk(index=index, start=float(index), end=float(index + 1), samples=[], vad_activation=activation, vad_peak=peak)


class VadSelectionTests(unittest.TestCase):
    def test_select_high_activation_chunks_targets_active_voice_duration_in_timeline_order(self) -> None:
        chunks = [_chunk(0, 0.1), _chunk(1, 0.9), _chunk(2, 0.2), _chunk(3, 0.8), _chunk(4, 0.3)]

        selected = select_high_activation_chunks(chunks, target_duration_ratio=0.4)

        self.assertEqual([chunk.index for chunk in selected], [1, 3])

    def test_select_high_activation_chunks_honors_minimum(self) -> None:
        chunks = [_chunk(0, 0.1), _chunk(1, 0.9), _chunk(2, 0.2)]

        selected = select_high_activation_chunks(chunks, target_duration_ratio=0.0, min_chunks=1)

        self.assertEqual([chunk.index for chunk in selected], [1])

    def test_long_stream_default_ratio_smoothly_ramps_down_by_media_duration(self) -> None:
        self.assertAlmostEqual(long_stream_default_duration_ratio(0.0), 0.15)
        self.assertAlmostEqual(long_stream_default_duration_ratio(5 * 3600.0), 0.07)
        self.assertGreater(long_stream_default_duration_ratio(2.5 * 3600.0), 0.07)
        self.assertLess(long_stream_default_duration_ratio(2.5 * 3600.0), 0.15)

    def test_cleanup_group_max_sec_is_clamped_from_media_duration(self) -> None:
        self.assertEqual(_cleanup_group_max_sec(90.0), 60.0)
        self.assertEqual(_cleanup_group_max_sec(360.0), 180.0)
        self.assertEqual(_cleanup_group_max_sec(3600.0), 600.0)

    def test_cleanup_group_policy_tiers(self) -> None:
        eight_gb = CleanupGroupPolicy(20.0, 8.0, 180.0)
        twelve_gb = CleanupGroupPolicy(40.0, 4.0, 300.0)
        sixteen_gb = CleanupGroupPolicy(60.0, 2.0, 600.0)
        self.assertEqual(_cleanup_group_max_sec(80.0, eight_gb), 20.0)
        self.assertEqual(_cleanup_group_max_sec(800.0, eight_gb), 100.0)
        self.assertEqual(_cleanup_group_max_sec(2400.0, eight_gb), 180.0)
        self.assertEqual(_cleanup_group_max_sec(80.0, twelve_gb), 40.0)
        self.assertEqual(_cleanup_group_max_sec(800.0, twelve_gb), 200.0)
        self.assertEqual(_cleanup_group_max_sec(2400.0, twelve_gb), 300.0)
        self.assertEqual(_cleanup_group_max_sec(80.0, sixteen_gb), 60.0)
        self.assertEqual(_cleanup_group_max_sec(800.0, sixteen_gb), 400.0)
        self.assertEqual(_cleanup_group_max_sec(2400.0, sixteen_gb), 600.0)

    def test_speech_timestamps_from_probabilities_splits_on_silence(self) -> None:
        timestamps = _speech_timestamps_from_probabilities(
            [0.1, 0.8, 0.9, 0.2, 0.1, 0.8, 0.9, 0.1, 0.1],
            window_size_samples=100,
            sample_rate=1000,
            total_samples=900,
            max_chunk_sec=10.0,
            min_speech_sec=0.1,
            min_silence_ms=100,
            speech_pad_ms=0,
        )

        self.assertEqual(timestamps, [{"start": 100, "end": 300}, {"start": 500, "end": 700}])

    def test_max_speech_cut_resets_trigger_before_following_silence(self) -> None:
        timestamps = _speech_timestamps_from_probabilities(
            [0.9] * 11 + [0.1] * 5 + [0.9] * 3,
            window_size_samples=100,
            sample_rate=1000,
            total_samples=1900,
            max_chunk_sec=1.1,
            min_speech_sec=0.1,
            min_silence_ms=300,
            speech_pad_ms=0,
        )

        self.assertEqual(timestamps, [{"start": 0, "end": 1100}, {"start": 1600, "end": 1900}])

    def test_max_speech_cut_keeps_active_span_after_candidate_silence(self) -> None:
        probabilities = [0.9, 0.9, 0.1, 0.1] + [0.9] * 7 + [0.4] + [0.9] * 7

        timestamps = _speech_timestamps_from_probabilities(
            probabilities,
            window_size_samples=100,
            sample_rate=1000,
            total_samples=1900,
            max_chunk_sec=1.1,
            min_speech_sec=0.1,
            min_silence_ms=500,
            speech_pad_ms=0,
        )

        self.assertEqual(
            timestamps,
            [
                {"start": 0, "end": 200},
                {"start": 400, "end": 1500},
                {"start": 1600, "end": 1900},
            ],
        )

    def test_cleanup_groups_split_oversized_runs_at_largest_gap(self) -> None:
        chunks = [
            AudioChunk(index=0, start=0.0, end=10.0, samples=[]),
            AudioChunk(index=1, start=11.0, end=20.0, samples=[]),
            AudioChunk(index=2, start=80.0, end=90.0, samples=[]),
            AudioChunk(index=3, start=91.0, end=100.0, samples=[]),
        ]

        groups = assign_vad_groups_by_largest_gaps(chunks, max_group_sec=50.0)

        self.assertEqual([(group.start, group.end) for group in groups], [(0.0, 20.0), (80.0, 100.0)])
        self.assertEqual([chunk.vad_group_index for chunk in chunks], [0, 0, 1, 1])


if __name__ == "__main__":
    unittest.main()
