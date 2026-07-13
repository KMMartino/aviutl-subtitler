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


if __name__ == "__main__":
    unittest.main()
