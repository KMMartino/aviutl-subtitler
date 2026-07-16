import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from subtitler.api_usage import ApiUsageLedger
from subtitler.external_refiners import GeminiTextRefiner, HostedTextRefiner, OpenAITextRefiner, _hosted_text_timeout
from subtitler.external_transcribers import DeadTranscriptionRequest, FallbackTranscriber, GeminiTranscriber, MalformedTranscriptionResponse, OpenAITranscriber, _hosted_transcription_timeout, _request_json, verify_gemini_model_available, verify_openai_model_available
from subtitler.glossary import GlossaryEntry
from subtitler.transcriber import UNTRANSCRIBABLE_AUDIO_TOKEN
from subtitler.models import AudioChunk


class ExternalApiClientTests(unittest.TestCase):
    def test_hosted_cleanup_prompt_treats_glossary_as_spelling_reference(self) -> None:
        refiner = HostedTextRefiner(
            "hosted-cleanup",
            [GlossaryEntry("State of Decay", "game title | Xbox")],
            ApiUsageLedger(),
        )

        prompt = refiner._prompt_one("ステートオブプレイについて話します")

        self.assertIn("not as a list of terms expected in the transcript", prompt)
        self.assertIn("close phonetic or orthographic match", prompt)
        self.assertIn("if the input is already a plausible different term, preserve it", prompt)
        self.assertIn("entries are not correction candidates", prompt)
        self.assertIn("State of Decay", prompt)

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

    def test_gemini_transcription_uses_api_key_header_not_query(self) -> None:
        response = {
            "candidates": [{"content": {"parts": [{"text": "どうも"}]}}],
            "usageMetadata": {},
        }
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.verify_gemini_model_available"), mock.patch(
                "subtitler.external_transcribers._request_json", return_value=response
            ) as request:
                GeminiTranscriber(
                    "gemini-2.5-flash", Path(temp_name), ApiUsageLedger(), api_key="secret-key"
                ).transcribe(self._chunk())
        _method, url, _payload, *_rest = request.call_args.args
        self.assertNotIn("secret-key", url)
        self.assertNotIn("?key=", url)
        self.assertEqual(request.call_args.kwargs["headers"], {"x-goog-api-key": "secret-key"})

    def test_gemini_3_transcription_does_not_force_discouraged_temperature(self) -> None:
        response = {
            "candidates": [{"content": {"parts": [{"text": "どうも"}]}}],
            "usageMetadata": {},
        }
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.verify_gemini_model_available"), mock.patch(
                "subtitler.external_transcribers._request_json", return_value=response
            ) as request:
                GeminiTranscriber(
                    "gemini-3.5-flash", Path(temp_name), ApiUsageLedger(), api_key="secret-key"
                ).transcribe(self._chunk())
        payload = request.call_args.args[2]
        self.assertNotIn("generationConfig", payload)

    def test_gemini_model_verification_uses_api_key_header_not_query(self) -> None:
        with mock.patch("subtitler.external_transcribers._request_json", return_value={"models": []}) as request:
            with self.assertRaises(Exception):
                verify_gemini_model_available("gemini-missing", "secret-key")
        _method, url, *_rest = request.call_args.args
        self.assertNotIn("secret-key", url)
        self.assertNotIn("?key=", url)
        self.assertEqual(request.call_args.kwargs["headers"], {"x-goog-api-key": "secret-key"})

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

    def test_openai_canonical_model_is_available_when_dated_alias_is_listed(self) -> None:
        response = {"data": [{"id": "gpt-4o-mini-transcribe-2025-12-15"}]}
        with mock.patch("subtitler.external_transcribers._request_json", return_value=response):
            available = verify_openai_model_available("gpt-4o-mini-transcribe", "key")

        self.assertEqual(available, "gpt-4o-mini-transcribe-2025-12-15")

    def test_openai_unrelated_model_is_rejected(self) -> None:
        response = {"data": [{"id": "gpt-5.4-mini"}]}
        with mock.patch("subtitler.external_transcribers._request_json", return_value=response):
            with self.assertRaises(Exception):
                verify_openai_model_available("gpt-4o-mini-transcribe", "key")

    def test_fallback_transcriber_exposes_primary_quality_failure(self) -> None:
        chunk = self._chunk()
        primary = mock.Mock(provider="gemini", model="gemini-3.5-flash")
        fallback = mock.Mock(provider="openai", model="gpt-4o-mini-transcribe")
        primary.transcribe.side_effect = MalformedTranscriptionResponse("empty response")
        with self.assertRaises(MalformedTranscriptionResponse):
            FallbackTranscriber(primary, fallback).transcribe(chunk)
        primary.transcribe.assert_called_once_with(chunk)
        fallback.transcribe.assert_not_called()

    def test_fallback_transcriber_exposes_primary_transport_failure(self) -> None:
        chunk = self._chunk()
        primary = mock.Mock(provider="gemini", model="gemini-3.5-flash")
        fallback = mock.Mock(provider="openai", model="gpt-4o-mini-transcribe")
        primary.transcribe.side_effect = DeadTranscriptionRequest("read operation timed out")
        with self.assertRaises(DeadTranscriptionRequest):
            FallbackTranscriber(primary, fallback).transcribe(chunk)
        primary.transcribe.assert_called_once_with(chunk)
        fallback.transcribe.assert_not_called()

    def test_transcription_timeout_request_can_be_classified_for_fallback(self) -> None:
        with mock.patch("subtitler.hosted_http.urllib.request.urlopen", side_effect=TimeoutError("timed out")), mock.patch(
            "subtitler.hosted_http.time.sleep"
        ), mock.patch("subtitler.hosted_http.random.uniform", return_value=0.0):
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

    def test_gemini_refiner_uses_api_key_header_not_query(self) -> None:
        response = {"candidates": [{"content": {"parts": [{"text": "clean"}]}}], "usageMetadata": {}}
        with mock.patch("subtitler.external_refiners.verify_gemini_model_available"), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ) as request:
            result = GeminiTextRefiner(
                "gemini-2.5-flash", [], ApiUsageLedger(), api_key="secret-key"
            )._chat("prompt")
        self.assertEqual(result, "clean")
        _method, url, *_rest = request.call_args.args
        self.assertNotIn("secret-key", url)
        self.assertNotIn("?key=", url)
        self.assertEqual(request.call_args.kwargs["headers"], {"x-goog-api-key": "secret-key"})

    def test_gemini_3_refiner_sends_thinking_level_without_forced_temperature(self) -> None:
        response = {"candidates": [{"content": {"parts": [{"text": "clean"}]}}], "usageMetadata": {}}
        with mock.patch("subtitler.external_refiners.verify_gemini_model_available"), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ) as request:
            result = GeminiTextRefiner(
                "gemini-3.5-flash",
                [],
                ApiUsageLedger(),
                api_key="secret-key",
                thinking_level="low",
            )._chat("prompt")
        self.assertEqual(result, "clean")
        generation_config = request.call_args.args[2]["generationConfig"]
        self.assertEqual(generation_config["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertNotIn("temperature", generation_config)

    def test_openai_refiner_sends_explicit_reasoning_effort(self) -> None:
        response = {
            "choices": [{"message": {"content": "clean"}}],
            "usage": {},
        }
        with mock.patch("subtitler.external_refiners.verify_openai_model_available"), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ) as request:
            result = OpenAITextRefiner(
                "gpt-5.6-luna",
                [],
                ApiUsageLedger(),
                api_key="secret-key",
                reasoning_effort="none",
            )._chat("prompt")
        self.assertEqual(result, "clean")
        payload = request.call_args.args[2]
        self.assertEqual(payload["reasoning_effort"], "none")

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
