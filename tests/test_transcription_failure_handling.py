import unittest

from subtitler.backends.existing_pipeline import FAILED_TRANSCRIPTION_TEXT, transcribe_one
from subtitler.errors import TranscriptionError
from subtitler.models import AudioChunk
from subtitler.profiling import PipelineProfiler


class FailingTranscriber:
    def transcribe(self, chunk):
        raise TranscriptionError("provider timeout")


class TranscriptionFailureHandlingTests(unittest.TestCase):
    def test_failed_worker_returns_failed_transcript_marker(self) -> None:
        chunk = AudioChunk(index=7, start=1.0, end=2.0, samples=[])
        transcript = transcribe_one(FailingTranscriber(), chunk, PipelineProfiler(False, None))

        self.assertEqual(transcript.chunk, chunk)
        self.assertEqual(transcript.text, FAILED_TRANSCRIPTION_TEXT)


if __name__ == "__main__":
    unittest.main()
