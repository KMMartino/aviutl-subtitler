import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.api_usage import ApiUsageLedger
from subtitler.errors import SubtitlerError
from subtitler.hosted_inspection import HostedInspectionProvider, extract_inspection_frames


SCHEMA = {"type": "object", "properties": {"visible": {"type": "boolean"}},
          "required": ["visible"], "additionalProperties": False}


def response(text='{"visible": true}', status="completed"):
    return {"status": status, "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "output_tokens_details": {"reasoning_tokens": 150}}}


class HostedInspectionTests(unittest.TestCase):
    def test_request_records_reasoning_cost_and_redacted_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "frame-000000001200.jpg"
            image.write_bytes(b"jpeg")
            ledger = ApiUsageLedger()
            provider = HostedInspectionProvider(ledger, 1, root / "diagnostics")
            with patch("subtitler.hosted_inspection.require_api_key", return_value="secret-key"), \
                 patch("subtitler.hosted_inspection.request_json", return_value=response()) as request:
                self.assertEqual(provider.inspect(operation="../verify", model="gpt-5.6-terra", prompt="inspect",
                                                  schema=SCHEMA, images=[image]), {"visible": True})
            payload = request.call_args.args[2]
            content = payload["input"][0]["content"]
            self.assertEqual(content[1]["text"], "Image 1; original source timestamp 1200 milliseconds.")
            self.assertEqual(content[2]["detail"], "high")
            self.assertEqual(request.call_args.kwargs["attempts"], 1)
            self.assertEqual(ledger.rows[0].output_tokens, 200)
            self.assertAlmostEqual(ledger.total_cost_usd, .0026)
            diagnostics = list((root / "diagnostics").glob("*.json"))
            self.assertEqual(len(diagnostics), 1)
            self.assertNotIn("secret-key", diagnostics[0].read_text())
            self.assertEqual(json.loads(diagnostics[0].read_text())["status"], "complete")

            cached = response()
            cached["usage"]["input_tokens_details"] = {"cache_write_tokens": 100}
            with patch("subtitler.hosted_inspection.require_api_key", return_value="secret-key"), \
                 patch("subtitler.hosted_inspection.request_json", return_value=cached):
                provider.inspect(operation="cache-write", model="gpt-5.6-terra", prompt="inspect", schema=SCHEMA)
            self.assertAlmostEqual(ledger.rows[-1].cost_usd, .00265)
            cached["usage"]["input_tokens_details"] = {"cached_tokens": 100}
            with patch("subtitler.hosted_inspection.require_api_key", return_value="secret-key"), \
                 patch("subtitler.hosted_inspection.request_json", return_value=cached):
                provider.inspect(operation="cache-read", model="gpt-5.6-terra", prompt="inspect", schema=SCHEMA)
            self.assertAlmostEqual(ledger.rows[-1].cost_usd, .00242)

    def test_budget_model_and_schema_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            for overrides in ({"budget": .000001}, {"model": "unknown"}, {"reasoning_effort": "high"},
                              {"schema": {"type": "object", "$ref": "anything"}}):
                with self.subTest(overrides=overrides):
                    options = dict(overrides)
                    provider = HostedInspectionProvider(ApiUsageLedger(), options.pop("budget", 1), Path(temporary))
                    arguments = dict(operation="verify", model="gpt-5.6-terra", prompt="inspect", schema=SCHEMA)
                    arguments.update(options)
                    with patch("subtitler.hosted_inspection.request_json") as request:
                        with self.assertRaises(SubtitlerError):
                            provider.inspect(**arguments)
                        request.assert_not_called()

    def test_invalid_responses_still_account_for_paid_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            for data in (response(status="incomplete"), response("garbage"), response('{"visible": 1}'),
                         response('{"visible": true, "extra": 1}')):
                ledger = ApiUsageLedger()
                provider = HostedInspectionProvider(ledger, 1, Path(temporary))
                with patch("subtitler.hosted_inspection.require_api_key", return_value="key"), \
                     patch("subtitler.hosted_inspection.request_json", return_value=data):
                    with self.assertRaises(SubtitlerError):
                        provider.inspect(operation="verify", model="gpt-5.6-terra", prompt="inspect", schema=SCHEMA)
                self.assertAlmostEqual(ledger.total_cost_usd, .0026)

    def test_unknown_usage_retains_reservation_and_prevents_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = ApiUsageLedger()
            provider = HostedInspectionProvider(ledger, .13, Path(temporary))
            with patch("subtitler.hosted_inspection.require_api_key", return_value="key"), \
                 patch("subtitler.hosted_inspection.request_json", side_effect=SubtitlerError("timeout")) as request:
                for _ in range(2):
                    with self.assertRaises(SubtitlerError):
                        provider.inspect(operation="verify", model="gpt-5.6-terra", prompt="inspect", schema=SCHEMA)
                    provider = HostedInspectionProvider(ledger, .13, Path(temporary))
                self.assertEqual(request.call_count, 1)
            self.assertEqual(ledger.rows[0].operation, "verify:unconfirmed_estimate")
            self.assertGreater(ledger.total_cost_usd, .1)
            diagnostic = json.loads(next(Path(temporary).glob("*.json")).read_text())
            self.assertTrue(diagnostic["cost_is_estimate"])

    def test_frame_extraction_is_bounded_and_checks_missing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def run(command, **kwargs):
                Path(command[-1]).write_bytes(b"jpeg")
                return subprocess.CompletedProcess(command, 0)
            with patch("subtitler.hosted_inspection.subprocess.run", side_effect=run) as subprocess_run:
                paths = extract_inspection_frames(root / "video.mkv", [1200, 2400], root)
                self.assertEqual([p.name for p in paths], ["frame-000000001200.jpg", "frame-000000002400.jpg"])
                self.assertIn("1.200", subprocess_run.call_args_list[0].args[0])
                with self.assertRaises(SubtitlerError):
                    extract_inspection_frames(root / "video.mkv", list(range(65)), root)
            with patch("subtitler.hosted_inspection.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
                with self.assertRaises(SubtitlerError):
                    extract_inspection_frames(root / "video.mkv", [3600], root)
