import unittest

import numpy as np

from subtitler.models import AudioChunk
from subtitler.vad import split_chunk_with_tighter_vad


class _CountingSession:
    def __init__(self) -> None:
        self.calls = 0

    def probabilities(self, samples, sample_rate, progress_callback=None):
        self.calls += 1
        # Two speech islands separated by enough silence for the first policy.
        return [0.9] * 20 + [0.0] * 12 + [0.9] * 20, 512


class VadReuseTests(unittest.TestCase):
    def test_split_policies_reuse_one_probability_pass(self) -> None:
        session = _CountingSession()
        samples = np.ones(52 * 512, dtype=np.float32)
        chunk = AudioChunk(index=3, start=10.0, end=10.0 + len(samples) / 16000, samples=samples)

        result = split_chunk_with_tighter_vad(chunk, 16000, session=session)

        self.assertEqual(session.calls, 1)
        self.assertGreaterEqual(len(result), 2)
        self.assertTrue(all(chunk.start <= item.start < item.end <= chunk.end for item in result))

    def test_hosted_recovery_coalesces_many_speech_islands(self) -> None:
        class ManyIslandsSession:
            def probabilities(self, samples, sample_rate, progress_callback=None):
                probabilities = []
                for _ in range(12):
                    probabilities.extend([0.9] * 10)
                    probabilities.extend([0.0] * 20)
                return probabilities, 512

        samples = np.ones(12 * 30 * 512, dtype=np.float32)
        duration = len(samples) / 16000
        chunk = AudioChunk(index=4, start=0.0, end=duration, samples=samples)

        result = split_chunk_with_tighter_vad(
            chunk,
            16000,
            session=ManyIslandsSession(),
            recovery_min_silence_ms=400,
            recovery_speech_pad_ms=200,
            max_pieces=2,
        )

        self.assertEqual(len(result), 2)
        self.assertTrue(all(item.end > item.start for item in result))


if __name__ == "__main__":
    unittest.main()
