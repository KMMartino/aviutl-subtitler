import contextlib
import io
import unittest
from unittest import mock

from subtitler.models import AudioChunk
from subtitler.transcriber import ServerGemmaTranscriber, UNTRANSCRIBABLE_AUDIO_TOKEN


def _chunk(index: int = 370) -> AudioChunk:
    return AudioChunk(index=index, start=0.0, end=3.0, samples=[])


def _transcriber() -> ServerGemmaTranscriber:
    transcriber = ServerGemmaTranscriber.__new__(ServerGemmaTranscriber)
    transcriber.max_transcription_split_depth = 2
    transcriber.glossary = None
    return transcriber


class LocalTranscriberRecoveryTests(unittest.TestCase):
    def test_usable_normal_transcript_returns_without_recovery(self) -> None:
        transcriber = _transcriber()
        transcriber._transcribe_payload_once = mock.Mock(return_value="正常な文字起こし")
        transcriber._recover_with_split = mock.Mock()

        text = transcriber.transcribe_payload(_chunk(), {"messages": []}, "直前の文")

        self.assertEqual(text, "正常な文字起こし")
        transcriber._recover_with_split.assert_not_called()

    def test_empty_transcript_runs_split_then_skips_context_without_predecessor(self) -> None:
        transcriber = _transcriber()
        transcriber._transcribe_payload_once = mock.Mock(return_value="")
        transcriber._recover_with_split = mock.Mock(return_value="")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            text = transcriber.transcribe_payload(_chunk(), {"messages": []})

        self.assertEqual(text, "")
        transcriber._recover_with_split.assert_called_once()
        self.assertEqual(transcriber._transcribe_payload_once.call_count, 1)
        self.assertIn("preceding transcript unavailable", output.getvalue())

    def test_failed_split_retries_original_with_context(self) -> None:
        transcriber = _transcriber()
        transcriber._transcribe_payload_once = mock.Mock(side_effect=["", "文脈で復帰しました"])
        transcriber._recover_with_split = mock.Mock(return_value="")
        transcriber.prepare_payload = mock.Mock(return_value={"contextual": True})
        chunk = _chunk()

        text = transcriber.transcribe_payload(chunk, {"normal": True}, "前の発話です")

        self.assertEqual(text, "文脈で復帰しました")
        transcriber.prepare_payload.assert_called_once_with(chunk, "前の発話です")
        self.assertEqual(transcriber._transcribe_payload_once.call_args_list[-1].args, (chunk, {"contextual": True}))

    def test_successful_split_prevents_context_retry(self) -> None:
        transcriber = _transcriber()
        transcriber._transcribe_payload_once = mock.Mock(return_value="")
        transcriber._recover_with_split = mock.Mock(return_value="分割で復帰しました")
        transcriber.prepare_payload = mock.Mock()

        text = transcriber.transcribe_payload(_chunk(), {"messages": []}, "前の発話")

        self.assertEqual(text, "分割で復帰しました")
        transcriber.prepare_payload.assert_not_called()

    def test_untranscribable_response_does_not_recover(self) -> None:
        transcriber = _transcriber()
        transcriber._transcribe_payload_once = mock.Mock(return_value=UNTRANSCRIBABLE_AUDIO_TOKEN)
        transcriber._recover_with_split = mock.Mock()

        self.assertEqual(transcriber.transcribe_payload(_chunk(), {"messages": []}, "前の発話"), "")
        transcriber._recover_with_split.assert_not_called()


if __name__ == "__main__":
    unittest.main()
