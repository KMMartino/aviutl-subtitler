import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aviutl_subtitle
from subtitler.config import load_workflow_config
from subtitler.errors import SubtitlerError


class WorkflowConfigLoadingTests(unittest.TestCase):
    def test_malformed_json_reports_path_and_parse_location(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "broken.json"
            path.write_text('{\n  "backend": }', encoding="utf-8")

            with self.assertRaises(SubtitlerError) as raised:
                load_workflow_config("local", path)

        message = str(raised.exception)
        self.assertIn(str(path), message)
        self.assertIn("line 2, column", message)

    def test_nonstandard_json_numeric_constants_are_rejected(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant), tempfile.TemporaryDirectory() as temp_name:
                path = Path(temp_name) / "invalid-number.json"
                path.write_text(f'{{"cost": {{"max_estimated_api_cost_usd": {constant}}}}}', encoding="utf-8")

                with self.assertRaisesRegex(SubtitlerError, "nonstandard numeric constant"):
                    load_workflow_config("hosted", path)

    def test_unreadable_config_is_normalized(self):
        path = Path("unreadable.json")
        with patch.object(Path, "read_text", side_effect=OSError("access denied")):
            with self.assertRaises(SubtitlerError) as raised:
                load_workflow_config("local", path)

        self.assertIn(str(path), str(raised.exception))
        self.assertIn("access denied", str(raised.exception))

    def test_cli_returns_user_facing_error_for_malformed_config(self):
        with tempfile.TemporaryDirectory() as temp_name:
            input_path = Path(temp_name) / "input.wav"
            config_path = Path(temp_name) / "broken.json"
            input_path.touch()
            config_path.write_text("{broken", encoding="utf-8")
            stderr = io.StringIO()
            argv = ["aviutl_subtitle.py", str(input_path), "--config", str(config_path)]

            with patch.object(aviutl_subtitle.sys, "argv", argv), contextlib.redirect_stderr(stderr):
                result = aviutl_subtitle.main()

        self.assertEqual(result, 1)
        self.assertIn("Error: Invalid JSON in workflow config", stderr.getvalue())
        self.assertIn(str(config_path), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
