import unittest

from subtitler.transcript_normalizer import backend_result_to_aligned_chunks, speech_regions_to_markers
from subtitler.transcription_backend import BackendTranscriptResult, SpeechRegion, TranscriptSegment, TranscriptToken


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

    def test_speech_regions_convert_to_markers(self):
        markers = speech_regions_to_markers([SpeechRegion(index=9, start=2.0, end=4.0, activation=0.5)])

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].start_time, 2.0)
        self.assertIn("a=0.50", markers[0].text)


if __name__ == "__main__":
    unittest.main()
