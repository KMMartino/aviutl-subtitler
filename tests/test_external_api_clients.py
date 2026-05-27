import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from subtitler.api_usage import ApiUsageLedger
from subtitler.external_refiners import GeminiTextRefiner, OpenAITextRefiner
from subtitler.external_transcribers import GeminiTranscriber, OpenAITranscriber
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
            ), mock.patch("subtitler.external_transcribers._request_json_with_retries", return_value=response):
                transcriber = GeminiTranscriber("gemini-2.5-flash", Path(temp_name), ledger)
                result = transcriber.transcribe(self._chunk())
        self.assertEqual(result.text, "どうも")
        self.assertEqual(ledger.rows[0].audio_input_tokens, 32)

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
            ), mock.patch("subtitler.external_transcribers._request_multipart_with_retries", return_value=response):
                transcriber = OpenAITranscriber("gpt-4o-transcribe", Path(temp_name), ledger)
                result = transcriber.transcribe(self._chunk())
        self.assertEqual(result.text, "こんにちは")
        self.assertEqual(ledger.rows[0].output_tokens, 5)

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


if __name__ == "__main__":
    unittest.main()
