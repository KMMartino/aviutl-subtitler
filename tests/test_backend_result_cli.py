import contextlib
import io
import unittest

from aviutl_subtitle import _handle_backend_result_status
from subtitler.errors import SubtitlerError
from subtitler.transcription_backend import BackendDiagnostic, BackendTranscriptResult


class BackendResultCliTests(unittest.TestCase):
    def test_failed_result_terminates_with_actionable_error(self) -> None:
        result = BackendTranscriptResult(backend_name="test", status="failed")

        with self.assertRaisesRegex(
            SubtitlerError,
            "selected speech produced no usable transcript segments",
        ):
            _handle_backend_result_status(result)

    def test_partial_result_warns_and_continues(self) -> None:
        result = BackendTranscriptResult(
            backend_name="test",
            status="partial",
            diagnostics=[
                BackendDiagnostic(
                    level="warning",
                    message="Transcription failed for chunk 3",
                    region_index=3,
                    code="transcription_failed",
                )
            ],
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            _handle_backend_result_status(result)

        self.assertIn("partial result", output.getvalue())
        self.assertIn("1 chunk(s) failed", output.getvalue())
        self.assertIn("continuing with the usable segments", output.getvalue())

    def test_ok_result_is_silent(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            _handle_backend_result_status(BackendTranscriptResult(backend_name="test"))

        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
