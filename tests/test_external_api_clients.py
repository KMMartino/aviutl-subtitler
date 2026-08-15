import io
import json
import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

import numpy as np

from subtitler.api_usage import ApiUsageLedger
from subtitler.external_refiners import GeminiTextRefiner, HostedTextRefiner, OpenAITextRefiner, _hosted_text_timeout
from subtitler.errors import StructuredOutputIncompleteError
from subtitler.external_transcribers import (
    DeadTranscriptionRequest,
    FallbackTranscriber,
    GeminiTranscriber,
    GPTTranscribeAdapter,
    MalformedTranscriptionResponse,
    OpenAITranscriber,
    _hosted_transcription_timeout,
    _multipart_body,
    _request_json,
    verify_gemini_model_available,
    verify_openai_model_available,
)
from subtitler.glossary import GlossaryEntry
from subtitler.models import AlignedToken, AudioChunk
from subtitler.splitter import split_token_chain
from subtitler.transcriber import UNTRANSCRIBABLE_AUDIO_TOKEN


class _JsonResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


def _retryable_http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test",
        code,
        "failure",
        Message(),
        io.BytesIO(b"failure"),
    )


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

    def test_gemini_37_transcription_pins_low_thinking(self) -> None:
        response = {
            "candidates": [{"content": {"parts": [{"text": "どうも"}]}}],
            "usageMetadata": {},
        }
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.verify_gemini_model_available"), mock.patch(
                "subtitler.external_transcribers._request_json", return_value=response
            ) as request:
                GeminiTranscriber(
                    "gemini-3.7-flash", Path(temp_name), ApiUsageLedger(), api_key="secret-key"
                ).transcribe(self._chunk())
        payload = request.call_args.args[2]
        self.assertEqual(payload["generationConfig"], {"thinkingConfig": {"thinkingLevel": "low"}})

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
                transcriber = OpenAITranscriber("test-transcription-model", Path(temp_name), ledger)
                result = transcriber.transcribe(self._chunk())
        self.assertEqual(result.text, "こんにちは")
        self.assertEqual(ledger.rows[0].output_tokens, 5)

    def test_openai_long_stream_accepts_sparse_nonempty_transcript(self) -> None:
        response = {"text": "短い発言", "usage": {}}
        with tempfile.TemporaryDirectory() as temp_name:
            wav_path = Path(temp_name) / "sparse.wav"
            wav_path.write_bytes(b"wav")
            chunk = AudioChunk(
                index=7,
                start=0.0,
                end=120.0,
                samples=np.zeros(1, dtype=np.float32),
                wav_path=wav_path,
            )
            with mock.patch("subtitler.external_transcribers.verify_openai_model_available"), mock.patch(
                "subtitler.external_transcribers._request_multipart", return_value=response
            ):
                strict = OpenAITranscriber(
                    "test-transcription-model",
                    Path(temp_name),
                    ApiUsageLedger(),
                    api_key="secret-key",
                )
                with self.assertRaises(MalformedTranscriptionResponse):
                    strict.transcribe(chunk)

                long_stream = OpenAITranscriber(
                    "test-transcription-model",
                    Path(temp_name),
                    ApiUsageLedger(),
                    api_key="secret-key",
                    allow_sparse_transcript=True,
                )
                result = long_stream.transcribe(chunk)

        self.assertEqual(result.text, "短い発言")

    def test_gpt_transcribe_adapter_uses_model_specific_context_fields(self) -> None:
        glossary = [
            GlossaryEntry("AviUtl"),
            GlossaryEntry("invalid<term>"),
            GlossaryEntry("line\nbreak"),
        ]
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.verify_openai_model_available"), mock.patch(
                "subtitler.external_transcribers._request_multipart",
                return_value={"text": "現在の区間"},
            ) as request:
                transcriber = GPTTranscribeAdapter(
                    "gpt-transcribe",
                    Path(temp_name),
                    ApiUsageLedger(),
                    glossary=glossary,
                    api_key="secret-key",
                    language="ja",
                )
                result = transcriber.transcribe(self._chunk(), previous_transcript="直前の区間")

        self.assertEqual(result.text, "現在の区間")
        fields = request.call_args.args[2]
        self.assertEqual(fields["model"], "gpt-transcribe")
        self.assertEqual(fields["languages"], ["ja"])
        self.assertEqual(fields["keywords"], ["AviUtl"])
        self.assertEqual(fields["prompt"], "直前の区間")
        self.assertEqual(fields["response_format"], "json")
        self.assertNotIn("language", fields)
        self.assertNotIn("temperature", fields)

    def test_gpt_transcribe_adapter_omits_previous_context_on_initial_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            with mock.patch("subtitler.external_transcribers.verify_openai_model_available"), mock.patch(
                "subtitler.external_transcribers._request_multipart",
                return_value={"text": "現在の区間"},
            ) as request:
                GPTTranscribeAdapter(
                    "gpt-transcribe",
                    Path(temp_name),
                    ApiUsageLedger(),
                    api_key="secret-key",
                ).transcribe(self._chunk())

        self.assertNotIn("prompt", request.call_args.args[2])

    def test_multipart_body_serializes_array_fields_with_bracket_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            wav_path = Path(temp_name) / "chunk.wav"
            wav_path.write_bytes(b"wav")
            body = _multipart_body(
                "boundary",
                {
                    "model": "gpt-transcribe",
                    "languages": ["ja", "en"],
                    "keywords": ["AviUtl"],
                },
                "file",
                wav_path,
            ).decode("utf-8")

        self.assertEqual(body.count('name="languages[]"'), 2)
        self.assertEqual(body.count('name="keywords[]"'), 1)
        self.assertNotIn('name="model[]"', body)

    def test_openai_configured_model_is_available_when_exact_id_is_listed(self) -> None:
        response = {"data": [{"id": "gpt-transcribe"}]}
        with mock.patch("subtitler.external_transcribers._request_json", return_value=response):
            available = verify_openai_model_available("gpt-transcribe", "key")

        self.assertEqual(available, "gpt-transcribe")

    def test_openai_unrelated_model_is_rejected(self) -> None:
        response = {"data": [{"id": "gpt-5.4-mini"}]}
        with mock.patch("subtitler.external_transcribers._request_json", return_value=response):
            with self.assertRaises(Exception):
                verify_openai_model_available("gpt-transcribe", "key")

    def test_fallback_transcriber_exposes_primary_quality_failure(self) -> None:
        chunk = self._chunk()
        primary = mock.Mock(provider="gemini", model="gemini-3.5-flash")
        fallback = mock.Mock(provider="openai", model="fallback-transcription")
        primary.transcribe.side_effect = MalformedTranscriptionResponse("empty response")
        with self.assertRaises(MalformedTranscriptionResponse):
            FallbackTranscriber(primary, fallback).transcribe(chunk)
        primary.transcribe.assert_called_once_with(chunk)
        fallback.transcribe.assert_not_called()

    def test_fallback_transcriber_exposes_primary_transport_failure(self) -> None:
        chunk = self._chunk()
        primary = mock.Mock(provider="gemini", model="gemini-3.5-flash")
        fallback = mock.Mock(provider="openai", model="fallback-transcription")
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
            with mock.patch.object(refiner, "_chat", return_value="Boundary: z1b"):
                result = refiner.select_split_boundaries(
                    "前半後半", "前半⟦Z1A⟧後⟦Z1B⟧半", ["Z1A", "Z1B"], 10
                )
        self.assertTrue(result.accepted)
        self.assertEqual(result.selected_ids, ["Z1B"])

    def test_refiner_multi_split_response_selects_one_id_per_zone(self) -> None:
        ledger = ApiUsageLedger()
        with mock.patch("subtitler.external_refiners.require_api_key", return_value="key"), mock.patch(
            "subtitler.external_refiners.verify_gemini_model_available"
        ):
            refiner = GeminiTextRefiner("gemini-2.5-flash", [], ledger)
            with mock.patch.object(refiner, "_chat", return_value="Z1A, Z1B, Z2B"):
                result = refiner.select_split_boundaries(
                    "一番目二番目三番目",
                    "一番⟦Z1A⟧目⟦Z1B⟧二番⟦Z2A⟧目⟦Z2B⟧三番目",
                    ["Z1A", "Z1B", "Z2A", "Z2B"],
                    4,
                    multiple=True,
                )
        self.assertTrue(result.accepted)
        self.assertEqual(result.selected_ids, ["Z1A", "Z2B"])

    def test_refiner_boundary_selection_rejects_unknown_ids(self) -> None:
        ledger = ApiUsageLedger()
        with mock.patch("subtitler.external_refiners.require_api_key", return_value="key"), mock.patch(
            "subtitler.external_refiners.verify_gemini_model_available"
        ):
            refiner = GeminiTextRefiner("gemini-2.5-flash", [], ledger)
            with mock.patch.object(refiner, "_chat", return_value="Z9Z"):
                result = refiner.select_split_boundaries(
                    "一番目二番目", "一番⟦Z1A⟧目二番目", ["Z1A"], 4
                )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "no_valid_boundary_id")

    def test_refiner_boundary_selection_does_not_require_transcript_reproduction(self) -> None:
        ledger = ApiUsageLedger()
        text = "あ" * 50 + "い" * 30
        with mock.patch("subtitler.external_refiners.require_api_key", return_value="key"), mock.patch(
            "subtitler.external_refiners.verify_gemini_model_available"
        ):
            refiner = GeminiTextRefiner("gemini-2.5-flash", [], ledger)
            with mock.patch.object(refiner, "_chat", return_value="Z1C,Z2A"):
                result = refiner.select_split_boundaries(
                    text,
                    f"{'あ' * 30}⟦Z1C⟧{'あ' * 20}{'い' * 20}⟦Z2A⟧{'い' * 10}",
                    ["Z1A", "Z1B", "Z1C", "Z2A"],
                    40,
                    multiple=True,
                )
        self.assertTrue(result.accepted)
        self.assertEqual(result.selected_ids, ["Z1C", "Z2A"])

    def test_hosted_group_cleanup_budget_scales_with_window_text(self) -> None:
        refiner = HostedTextRefiner("hosted-cleanup", [], ApiUsageLedger())
        lines = ["あ" * 40 for _ in range(40)]
        with mock.patch.object(refiner, "_chat", return_value="\n".join(lines)) as chat:
            self.assertEqual(refiner._refine_many(lines), lines)
        self.assertEqual(chat.call_args.kwargs["max_tokens"], 9600)

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

    def test_openai_editorial_request_uses_schema_and_editorial_system_prompt(self) -> None:
        response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "subtitler.external_refiners.verify_openai_model_available"
        ), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ) as request:
            diagnostics = Path(directory) / "responses.jsonl"
            refiner = OpenAITextRefiner(
                "gpt-5.6-luna",
                [],
                ApiUsageLedger(),
                api_key="secret-key",
                reasoning_effort="low",
                structured_diagnostics_path=diagnostics,
            )
            result = refiner.complete_structured(
                "map this",
                max_tokens=16_384,
                operation="editorial_map",
                response_schema=schema,
            )
            record = json.loads(diagnostics.read_text(encoding="utf-8"))

        self.assertEqual(result, "{}")
        payload = request.call_args.args[2]
        self.assertIn("senior long-form video editor", payload["messages"][0]["content"])
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["max_completion_tokens"], 16_384)
        self.assertEqual(record["finish_reason"], "stop")
        self.assertEqual(record["response_content"], "{}")

    def test_openai_editorial_length_finish_is_diagnostic_and_retryable(self) -> None:
        response = {
            "choices": [{"finish_reason": "length", "message": {"content": '{"partial":'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 64, "total_tokens": 164},
        }
        ledger = ApiUsageLedger()
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "subtitler.external_refiners.verify_openai_model_available"
        ), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ):
            diagnostics = Path(directory) / "responses.jsonl"
            refiner = OpenAITextRefiner(
                "gpt-5.6-luna",
                [],
                ledger,
                api_key="secret-key",
                structured_diagnostics_path=diagnostics,
            )
            with self.assertRaises(StructuredOutputIncompleteError) as raised:
                refiner.complete_structured(
                    "map this",
                    max_tokens=64,
                    operation="editorial_map",
                    response_schema={"type": "object"},
                )
            record = json.loads(diagnostics.read_text(encoding="utf-8"))

        self.assertEqual(raised.exception.reason, "max_output_tokens")
        self.assertEqual(len(ledger.rows), 1)
        self.assertEqual(record["finish_reason"], "length")
        self.assertEqual(record["usage"]["completion_tokens"], 64)

    def test_openai_split_planning_disables_reasoning_without_changing_cleanup_profile(self) -> None:
        response = {
            "choices": [{"message": {"content": "Z1A"}}],
            "usage": {},
        }
        with mock.patch("subtitler.external_refiners.verify_openai_model_available"), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ) as request:
            refiner = OpenAITextRefiner(
                "gpt-5.4-mini",
                [],
                ApiUsageLedger(),
                api_key="secret-key",
                reasoning_effort="medium",
            )
            refiner._chat("split prompt", operation="split")
            split_payload = request.call_args.args[2]
            refiner._chat("cleanup prompt", operation="cleanup")
            cleanup_payload = request.call_args.args[2]
        self.assertEqual(split_payload["reasoning_effort"], "none")
        self.assertEqual(cleanup_payload["reasoning_effort"], "medium")

    def test_gemini_split_planning_uses_less_thinking_without_changing_cleanup_profile(self) -> None:
        response = {"candidates": [{"content": {"parts": [{"text": "Z1A"}]}}], "usageMetadata": {}}
        with mock.patch("subtitler.external_refiners.verify_gemini_model_available"), mock.patch(
            "subtitler.external_refiners._request_json_with_retries", return_value=response
        ) as request:
            refiner = GeminiTextRefiner(
                "gemini-3.5-flash",
                [],
                ApiUsageLedger(),
                api_key="secret-key",
                thinking_level="high",
            )
            refiner._chat("split prompt", operation="split")
            split_config = request.call_args.args[2]["generationConfig"]
            refiner._chat("cleanup prompt", operation="cleanup")
            cleanup_config = request.call_args.args[2]["generationConfig"]

        self.assertEqual(split_config["thinkingConfig"], {"thinkingLevel": "minimal"})
        self.assertEqual(cleanup_config["thinkingConfig"], {"thinkingLevel": "high"})

    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.random.uniform", return_value=0.0)
    def test_long_split_survives_transient_500_and_a_response_slower_than_old_budget(
        self, _uniform, _sleep
    ) -> None:
        response = _JsonResponse(
            b'{"choices":[{"message":{"content":"Z1A"}}],"usage":{}}'
        )
        observed_timeouts: list[float] = []
        calls = 0

        def serve(_request, *, timeout: float):
            nonlocal calls
            calls += 1
            observed_timeouts.append(timeout)
            if calls == 1:
                raise _retryable_http_error(500)
            if timeout <= 60.0:
                raise TimeoutError("simulated response needs 60 seconds")
            return response

        with mock.patch("subtitler.external_refiners.verify_openai_model_available"), mock.patch(
            "subtitler.hosted_http.urllib.request.urlopen", side_effect=serve
        ):
            refiner = OpenAITextRefiner(
                "gpt-5.4-mini", [], ApiUsageLedger(), api_key="secret-key"
            )
            result = refiner.select_split_boundaries(
                "あ" * 80,
                f"{'あ' * 40}⟦Z1A⟧{'あ' * 40}",
                ["Z1A"],
                40,
            )

        self.assertTrue(result.accepted)
        self.assertEqual(result.input_text, "あ" * 80)
        self.assertEqual(calls, 2)
        self.assertTrue(all(timeout > 60.0 for timeout in observed_timeouts))
        self.assertEqual(
            [attempt["outcome"] for attempt in result.request_attempts],
            ["http_error", "success"],
        )
        serialized_attempts = repr(result.request_attempts)
        self.assertNotIn("secret-key", serialized_attempts)
        self.assertNotIn("あ" * 20, serialized_attempts)

    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.random.uniform", return_value=0.0)
    def test_exhausted_split_requests_preserve_source_via_deterministic_fallback(
        self, _uniform, _sleep
    ) -> None:
        text = "あ漢" * 35
        tokens = [
            AlignedToken(char, index * 0.05, (index + 1) * 0.05, "char")
            for index, char in enumerate(text)
        ]
        with mock.patch("subtitler.external_refiners.verify_openai_model_available"), mock.patch(
            "subtitler.hosted_http.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            refiner = OpenAITextRefiner(
                "gpt-5.4-mini", [], ApiUsageLedger(), api_key="secret-key"
            )
            subtitles = split_token_chain(
                tokens,
                max_chars=40,
                max_duration=60.0,
                llm_splitter=refiner,
            )

        self.assertEqual("".join(subtitle.text for subtitle in subtitles), text)
        self.assertTrue(all(len(subtitle.text) <= 40 for subtitle in subtitles))

    def test_transcription_timeout_scales_with_audio_length_and_caps(self) -> None:
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 2.0, [])), 5.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 5.0, []), "gemini-3.1-flash-lite"), 5.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gemini-3.5-flash"), 30.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 2.0, []), "gemini-3.7-flash"), 20.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 7.82, []), "gemini-3.7-flash"), 36.28)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gemini-3.7-flash"), 125.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gemini-3.1-pro-preview"), 60.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "gpt-transcribe"), 60.0)
        self.assertEqual(_hosted_transcription_timeout(AudioChunk(1, 0.0, 30.0, []), "test-transcription-model", 2.0), 60.0)

    def test_text_timeout_has_floor_and_cap(self) -> None:
        self.assertEqual(_hosted_text_timeout("short", 32), 45.0)
        self.assertEqual(_hosted_text_timeout("x" * 12000, 1024), 240.96)
        self.assertEqual(_hosted_text_timeout("x" * 100000, 8192), 600.0)


if __name__ == "__main__":
    unittest.main()
