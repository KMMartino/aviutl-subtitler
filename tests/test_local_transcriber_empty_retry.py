import contextlib
import io
import unittest

from subtitler.models import AudioChunk
from subtitler.transcriber import ServerGemmaTranscriber


def _chunk(index: int = 370) -> AudioChunk:
    return AudioChunk(index=index, start=0.0, end=3.0, samples=[])


class LocalTranscriberEmptyRetryTests(unittest.TestCase):
    def test_empty_llama_server_transcript_retries_then_returns_empty(self) -> None:
        transcriber = ServerGemmaTranscriber.__new__(ServerGemmaTranscriber)
        transcriber.max_transcription_split_depth = 2
        calls = []

        def empty_once(chunk, payload):
            calls.append((chunk, payload))
            return ""

        transcriber._transcribe_payload_once = empty_once

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            text = transcriber.transcribe_payload(_chunk(), {"messages": []})

        self.assertEqual(text, "")
        self.assertEqual(len(calls), 2)
        self.assertIn("retrying attempt 2/2", output.getvalue())
        self.assertIn("skipping this chunk", output.getvalue())

    def test_empty_llama_server_transcript_can_recover_on_retry(self) -> None:
        transcriber = ServerGemmaTranscriber.__new__(ServerGemmaTranscriber)
        transcriber.max_transcription_split_depth = 2
        responses = ["", "復帰しました"]

        def recover_on_retry(chunk, payload):
            return responses.pop(0)

        transcriber._transcribe_payload_once = recover_on_retry

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            text = transcriber.transcribe_payload(_chunk(), {"messages": []})

        self.assertEqual(text, "復帰しました")
        self.assertEqual(responses, [])
        self.assertIn("retrying attempt 2/2", output.getvalue())
        self.assertNotIn("skipping this chunk", output.getvalue())


if __name__ == "__main__":
    unittest.main()
