import unittest

from subtitler.transcript_normalizer import backend_result_to_aligned_chunks
from subtitler.backends.existing_pipeline import transcription_result_status
from subtitler.transcription_backend import BackendDiagnostic, BackendTranscriptResult, TranscriptSegment


class TranscriptionBackendContractTests(unittest.TestCase):
    def test_existing_pipeline_reports_ok_when_all_selected_speech_is_usable(self):
        self.assertEqual(
            transcription_result_status(
                selected_chunk_count=2,
                usable_segment_count=2,
                failed_chunk_count=0,
            ),
            "ok",
        )

    def test_existing_pipeline_reports_partial_when_any_chunk_failed(self):
        self.assertEqual(
            transcription_result_status(
                selected_chunk_count=2,
                usable_segment_count=1,
                failed_chunk_count=1,
            ),
            "partial",
        )

    def test_existing_pipeline_reports_failed_when_selected_speech_has_no_usable_segment(self):
        for failed_chunk_count in (0, 2):
            with self.subTest(failed_chunk_count=failed_chunk_count):
                self.assertEqual(
                    transcription_result_status(
                        selected_chunk_count=2,
                        usable_segment_count=0,
                        failed_chunk_count=failed_chunk_count,
                    ),
                    "failed",
                )

    def test_existing_pipeline_treats_no_selected_speech_as_valid_empty_result(self):
        self.assertEqual(
            transcription_result_status(
                selected_chunk_count=0,
                usable_segment_count=0,
                failed_chunk_count=0,
            ),
            "ok",
        )

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
