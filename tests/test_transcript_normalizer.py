import unittest

from subtitler.transcript_normalizer import backend_result_to_aligned_chunks, speech_regions_to_markers
from subtitler.transcription_backend import (
    BackendTranscriptResult,
    RawVadSpeechInterval,
    SpeechRegion,
    TranscriptSegment,
    TranscriptToken,
)


class TranscriptNormalizerTests(unittest.TestCase):
    def test_timed_tokens_convert_to_aligned_tokens(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(
                    index=2,
                    text="hello world",
                    start=1.0,
                    end=3.0,
                    tokens=[
                        TranscriptToken("hello", 1.0, 2.0, "word"),
                        TranscriptToken("world", 2.0, 3.0, "word"),
                    ],
                )
            ],
            speech_regions=[SpeechRegion(index=2, start=1.0, end=3.0, activation=0.8, peak=0.9)],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual(len(chunks), 1)
        self.assertFalse(chunks[0].fallback)
        self.assertEqual([token.text for token in chunks[0].tokens], ["hello", "world"])
        self.assertEqual(chunks[0].chunk.vad_activation, 0.8)

    def test_tokenless_segment_becomes_fallback_chunk(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[TranscriptSegment(index=1, text="fallback", start=0.0, end=1.0)],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].fallback)
        self.assertEqual(chunks[0].tokens, [])

    def test_silence_aligned_trailing_punctuation_uses_spoken_endpoint(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(
                    index=1,
                    text="ナイス。",
                    start=7060.600,
                    end=7064.698,
                    tokens=[
                        TranscriptToken("ナ", 7060.600, 7060.798, "char"),
                        TranscriptToken("イ", 7060.798, 7060.918, "char"),
                        TranscriptToken("ス", 7060.918, 7064.698, "char"),
                        TranscriptToken("。", 7064.698, 7064.698, "char"),
                    ],
                )
            ],
            raw_vad_speech_intervals=[RawVadSpeechInterval(7060.600, 7061.384)],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual(chunks[0].tokens[-2].end, 7061.384)
        self.assertEqual(
            (chunks[0].tokens[-1].start, chunks[0].tokens[-1].end),
            (7061.384, 7061.384),
        )

    def test_leading_unsupported_punctuation_uses_following_spoken_boundary(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(
                    index=1,
                    text="…続く",
                    start=1.0,
                    end=5.0,
                    tokens=[
                        TranscriptToken("…", 1.0, 1.1, "char"),
                        TranscriptToken("続く", 5.0, 5.8, "word"),
                    ],
                )
            ],
            raw_vad_speech_intervals=[RawVadSpeechInterval(5.0, 5.8)],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual((chunks[0].tokens[0].start, chunks[0].tokens[0].end), (5.0, 5.0))

    def test_acoustically_supported_punctuation_keeps_its_alignment(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(
                    index=1,
                    text="はい！",
                    start=1.0,
                    end=2.0,
                    tokens=[
                        TranscriptToken("はい", 1.0, 1.8, "word"),
                        TranscriptToken("！", 1.8, 2.0, "char"),
                    ],
                )
            ],
            raw_vad_speech_intervals=[RawVadSpeechInterval(1.0, 2.0)],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual((chunks[0].tokens[-1].start, chunks[0].tokens[-1].end), (1.8, 2.0))

    def test_segment_group_metadata_overrides_same_index_fine_region(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(
                    index=1,
                    text="grouped",
                    start=10.0,
                    end=20.0,
                    metadata={"vad_group_index": 7},
                )
            ],
            speech_regions=[
                SpeechRegion(
                    index=1,
                    start=1.0,
                    end=2.0,
                    metadata={"vad_group_index": 0},
                )
            ],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual(chunks[0].chunk.vad_group_index, 7)

    def test_speech_regions_convert_to_markers(self):
        markers = speech_regions_to_markers([SpeechRegion(index=9, start=2.0, end=4.0, activation=0.5)])

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].start_time, 2.0)
        self.assertIn("a=0.50", markers[0].text)


if __name__ == "__main__":
    unittest.main()
