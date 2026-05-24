import unittest

from subtitler.alignment_pool import _split_transcript_for_subchunks
from subtitler.models import AudioChunk, TranscriptChunk


class AlignmentRetrySplitTests(unittest.TestCase):
    def test_japanese_transcript_is_partitioned_across_subchunk_durations(self):
        samples = [0.0] * 16000
        parent = AudioChunk(index=5, start=10.0, end=14.0, samples=samples)
        subchunks = [
            AudioChunk(index=5, start=10.0, end=11.0, samples=samples[:4000]),
            AudioChunk(index=5, start=11.0, end=14.0, samples=samples[4000:]),
        ]

        transcripts = _split_transcript_for_subchunks(
            TranscriptChunk(parent, "あいうえおかきく"),
            subchunks,
            "ja",
        )

        self.assertEqual([item.text for item in transcripts], ["あい", "うえおかきく"])
        self.assertEqual([item.chunk.start for item in transcripts], [10.0, 11.0])
        self.assertEqual([item.chunk.end for item in transcripts], [11.0, 14.0])


if __name__ == "__main__":
    unittest.main()
