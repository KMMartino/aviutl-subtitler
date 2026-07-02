import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from subtitler.api_usage import ApiUsageLedger
from subtitler.external_refiners import GeminiTextRefiner, OpenAITextRefiner, _hosted_text_timeout
from subtitler.external_transcribers import DeadTranscriptionRequest, FallbackTranscriber, GeminiTranscriber, MalformedTranscriptionResponse, OpenAITranscriber, _hosted_transcription_timeout, _request_json
from subtitler.transcriber import UNTRANSCRIBABLE_AUDIO_TOKEN
from subtitler.models import AudioChunk


class ExternalApiClientTests(unittest.TestCase):
    def _chunk(self) -> AudioChunk:
        return AudioChunk(index=1, start=0.0, end=1.0, samples=np.zeros(16000, dtype=np.float32))

    def test_gemini_transcriber_parses_text_and_usage(self) -> None:
        ledger = ApiUsageLedger()
        response = {
            "candidates": [{"content": {"parts": [{"text": "どうも"}]}}],
            "usageMetadata": {
                "promptTokenCount": 40,
                "candidatesTokenCount": 3,
                "totalTokenCount": 43,
                "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 32}],
            },
        }
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.require_api_key", return_value="key"), mock.patch(
                "subtitler.external_transcribers.verify_gemini_model_available"
            ), mock.patch("subtitler.external_transcribers._request_json", return_value=response):
                transcriber = GeminiTranscriber("gemini-2.5-flash", Path(temp_name), ledger)
                result = transcriber.transcribe(self._chunk())
        self.assertEqual(result.text, "どうも")
        self.assertEqual(ledger.rows[0].audio_input_tokens, 32)

    def test_gemini_untranscribable_token_returns_empty_transcript(self) -> None:
        ledger = ApiUsageLedger()
        response = {
            "candidates": [{"content": {"parts": [{"text": UNTRANSCRIBABLE_AUDIO_TOKEN}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.require_api_key", return_value="key"), mock.patch(
                "subtitler.external_transcribers.verify_gemini_model_available"
            ), mock.patch("subtitler.external_transcribers._request_json", return_value=response):
                transcriber = GeminiTranscriber("gemini-2.5-flash", Path(temp_name), ledger)
                result = transcriber.transcribe(self._chunk())
        self.assertEqual(result.text, "")
        self.assertEqual(len(ledger.rows), 1)

    def test_openai_transcriber_parses_text_and_usage(self) -> None:
        ledger = ApiUsageLedger()
        response = {
            "text": "こんにちは",
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
                "prompt_tokens_details": {"audio_tokens": 10},
            },
        }
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.require_api_key", return_value="key"), mock.patch(
                "subtitler.external_transcribers.verify_openai_model_available"
            ), mock.patch("subtitler.external_transcribers._request_multipart", return_value=response):
                transcriber = OpenAITranscriber("gpt-4o-transcribe", Path(temp_name), ledger)
                result = transcriber.transcribe(self._chunk())
        self.assertEqual(result.text, "こんにちは")
        self.assertEqual(ledger.rows[0].output_tokens, 5)

    def test_fallback_transcriber_uses_fallback_on_malformed_response(self) -> None:
        chunk = self._chunk()
        primary = mock.Mock(provider="gemini", model="gemini-3.5-flash")
        fallback = mock.Mock(provider="openai", model="gpt-4o-mini-transcribe")
        primary.transcribe.side_effect = MalformedTranscriptionResponse("empty response")
        fallback.transcribe.return_value = mock.Mock(text="fallback transcript")

        result = FallbackTranscriber(primary, fallback).transcribe(chunk)

        self.assertEqual(result.text, "fallback transcript")
        primary.transcribe.assert_called_once_with(chunk)
        fallback.transcribe.assert_called_once_with(chunk)

    def test_fallback_transcriber_uses_fallback_on_dead_request(self) -> None:
        chunk = self._chunk()
        primary = mock.Mock(provider="gemini", model="gemini-3.5-flash")
        fallback = mock.Mock(provider="openai", model="gpt-4o-mini-transcribe")
        primary.transcribe.side_effect = DeadTranscriptionRequest("read operation timed out")
        fallback.transcribe.return_value = mock.Mock(text="fallback transcript")

        result = FallbackTranscriber(primary, fallback).transcribe(chunk)

        self.assertEqual(result.text, "fallback transcript")
        primary.transcribe.assert_called_once_with(chunk)
        fallback.transcribe.assert_called_once_with(chunk)

    def test_transcription_timeout_request_can_be_classified_for_fallback(self) -> None:
        with mock.patch("subtitler.external_transcribers.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(DeadTranscriptionRequest):
                _request_json(
                    "GET",
                    "https://example.test",
                    None,
                    Exception,
                    "request failed",
                    timeout_sec=0.01,
                    dead_request_error_type=DeadTranscriptionRequest,
                )

    def test_missing_openai_key_is_rejected(self) -> None:
        previous = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(Exception):
                OpenAITextRefiner("gpt-5.4-mini", [], ApiUsageLedger())
        finally:
            if previous is not None:
                os.environ["OPENAI_API_KEY"] = previous

    def test_refiner_split_response_is_parsed(self) -> None:
        ledger = ApiUsageLedger()
        with mock.patch("subtitler.external_refiners.require_api_key", return_value="key"), mock.patch(
            "subtitler.external_refiners.verify_gemini_model_available"
        ):
            refiner = GeminiTextRefiner("gemini-2.5-flash", [], ledger)
            with mock.patch.object(refiner, "_chat", return_value="前半<SPLIT>後半"):
                result = refiner.split_lines_with_diagnostics("前半後半", 10)
        self.assertTrue(result.accepted)
        self.assertEqual(result.lines, ["前半", "後半"])

    def test_transcription_timeout_scales_with_audio_length_and_caps(self) -> None:
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 2.0, [])), 5.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 5.0, []), "gemini-3.1-flash-lite"), 5.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gemini-3.5-flash"), 30.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gemini-3.1-pro-preview"), 60.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gpt-4o-transcribe"), 60.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gpt-4o-mini-transcribe", 2.0), 60.0)

    def test_text_timeout_has_floor_and_cap(self) -> None:
        self.assertEqual(_hosted_text_timeout("short", 32), 45.0)
        self.assertEqual(_hosted_text_timeout("x" * 12000, 1024), 240.96)
        self.assertEqual(_hosted_text_timeout("x" * 100000, 8192), 600.0)


if __name__ == "__main__":
    unittest.main()
