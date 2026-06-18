import unittest

from subtitler.transcript_normalizer import backend_result_to_aligned_chunks
from subtitler.transcription_backend import BackendDiagnostic, BackendTranscriptResult, TranscriptSegment


class TranscriptionBackendContractTests(unittest.TestCase):
    def test_segments_are_consumed_in_timeline_order(self):
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(index=2, text="second", start=2.0, end=3.0),
                TranscriptSegment(index=1, text="first", start=0.0, end=1.0),
            ],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual([chunk.text for chunk in chunks], ["first", "second"])

    def test_failed_diagnostics_do_not_create_subtitle_text(self):
        result = BackendTranscriptResult(
            backend_name="test",
            diagnostics=[
                BackendDiagnostic(
                    level="warning",
                    message="Transcription failed for chunk 3",
                    region_index=3,
                    code="transcription_failed",
                )
            ],
            segments=[],
        )

        chunks = backend_result_to_aligned_chunks(result)

        self.assertEqual(chunks, [])
        self.assertEqual(result.diagnostics[0].code, "transcription_failed")


if __name__ == "__main__":
    unittest.main()
