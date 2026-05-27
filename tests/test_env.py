import os
import tempfile
import unittest
from pathlib import Path

from subtitler.env import load_env_file


class EnvFileTests(unittest.TestCase):
    def test_loads_basic_api_keys_without_overriding_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "OPENAI_API_KEY=from-file",
                        "GEMINI_API_KEY='quoted-value'",
                        'DEEPGRAM_API_KEY="double-quoted"',
                    ]
                ),
                encoding="utf-8",
            )
            previous = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "existing"
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("DEEPGRAM_API_KEY", None)
            try:
                loaded = load_env_file(path)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "existing")
                self.assertEqual(os.environ["GEMINI_API_KEY"], "quoted-value")
                self.assertEqual(os.environ["DEEPGRAM_API_KEY"], "double-quoted")
                self.assertEqual(loaded, ["GEMINI_API_KEY", "DEEPGRAM_API_KEY"])
            finally:
                if previous is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = previous
                os.environ.pop("GEMINI_API_KEY", None)
                os.environ.pop("DEEPGRAM_API_KEY", None)

    def test_missing_file_is_noop(self) -> None:
        self.assertEqual(load_env_file(Path("does-not-exist.env")), [])


if __name__ == "__main__":
    unittest.main()
