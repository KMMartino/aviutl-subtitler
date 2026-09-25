import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from subtitler.api_costs import token_cost
from subtitler.api_usage import ApiUsageLedger
from subtitler.backends.existing_pipeline import hosted_group_limit, uses_larger_hosted_transcription_segments
from subtitler.config import load_workflow_config, validate_workflow_config
from subtitler.errors import StructuredOutputIncompleteError, TranscriptionError
from subtitler.hosted_selection import select_hosted_models
from subtitler.models import AudioChunk
from subtitler.qwen import QwenTextRefiner, QwenTranscriber


class HostedHierarchyTests(unittest.TestCase):
    def test_each_provider_combination_selects_stages_independently(self):
        keys = ["GEMINI_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"]
        for mask in range(1, 8):
            env = {k: "test" for i, k in enumerate(keys) if mask & (1 << i)}
            with self.subTest(mask=mask), patch.dict(os.environ, env, clear=True):
                config = load_workflow_config("hosted")
                select_hosted_models(config)
                expected_asr = "gemini" if mask & 1 else "openai" if mask & 2 else "dashscope"
                expected_cleanup = "openai" if mask & 2 else "gemini" if mask & 1 else "dashscope"
                self.assertEqual(config["backend"]["transcriber"], expected_asr)
                self.assertEqual(config["cleanup"]["backend"], expected_cleanup)
                validate_workflow_config(config, workflow="hosted", check_paths=False)
                if len(env) == 1:
                    self.assertEqual(config["backend"]["fallback_transcriber"], "")

    def test_manual_and_local_choices_are_preserved(self):
        for workflow in ("local", "hosted"):
            config = load_workflow_config(workflow)
            config["backend"]["auto_select_hosted_models"] = workflow == "local"
            original = str(config)
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}, clear=True):
                select_hosted_models(config)
            self.assertEqual(str(config), original)

    def test_group_limits_cover_fallback_and_leave_local_short(self):
        config = load_workflow_config("hosted")
        self.assertTrue(uses_larger_hosted_transcription_segments(config))
        self.assertEqual(hosted_group_limit(config), 450)
        config["backend"]["fallback_transcriber"] = "dashscope"
        self.assertEqual(hosted_group_limit(config), 295)
        self.assertFalse(uses_larger_hosted_transcription_segments(load_workflow_config("local")))

    @patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test", "DASHSCOPE_BASE_URL": ""}, clear=True)
    def test_qwen_audio_language_endpoint_cost_and_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder)/"audio.wav"
            wav.write_bytes(b"audio")
            usage = ApiUsageLedger()
            client = QwenTranscriber("qwen-audio-3.1-asr-flash", Path(folder), usage, language="ja")
            chunk = AudioChunk(index=0, start=0, end=1, samples=[], wav_path=wav)
            with patch("subtitler.qwen.request_json", return_value={"output": {"text": "こんにちは"}, "usage": {"input_tokens": 100, "output_tokens": 20}}) as call:
                self.assertEqual(client.transcribe(chunk).text, "こんにちは")
                payload = call.call_args.args[2]
                self.assertEqual(payload["parameters"]["language_hints"], ["ja"])
                self.assertIn("dashscope-intl.aliyuncs.com/api/v1/", call.call_args.args[1])
                self.assertGreater(usage.total_cost_usd, 0)
                chunk.end = 301
                with self.assertRaises(TranscriptionError):
                    client.transcribe(chunk)
                self.assertEqual(call.call_count, 1)

    def test_qwen_cleanup_prices_follow_input_context_tiers(self):
        self.assertAlmostEqual(token_cost("dashscope", "qwen3.7-flash", input_tokens=32_000, output_tokens=1000), .00109)
        self.assertAlmostEqual(token_cost("dashscope", "qwen3.7-flash", input_tokens=32_001, output_tokens=1000), .0036001)
        self.assertAlmostEqual(token_cost("dashscope", "qwen3.7-flash", input_tokens=256_001, output_tokens=1000), .0520002)

    @patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test", "DASHSCOPE_BASE_URL": ""}, clear=True)
    def test_qwen_cleanup_disables_thinking_and_rejects_truncation(self):
        usage = ApiUsageLedger()
        client = QwenTextRefiner("qwen3.7-flash", [], usage)
        response = {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
        with patch("subtitler.qwen.request_json", return_value=response) as call:
            with self.assertRaises(StructuredOutputIncompleteError):
                client._chat("Japanese subtitle")
        self.assertFalse(call.call_args.args[2]["enable_thinking"])
        self.assertAlmostEqual(usage.total_cost_usd, token_cost("dashscope", "qwen3.7-flash", input_tokens=1000, output_tokens=100))
