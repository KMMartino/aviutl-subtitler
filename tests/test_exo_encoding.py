import sys
import tempfile
import unittest
from pathlib import Path

from subtitler.errors import ExoWriteError
from subtitler.exo import encode_text_for_exo, generate_exo_file, write_exo
from subtitler.models import ExoSettings, Subtitle


class ExoEncodingTests(unittest.TestCase):
    def test_japanese_subtitle_payload_writes_without_loss(self) -> None:
        subtitle_text = "日本語の字幕です"
        content = generate_exo_file(
            [Subtitle(0.0, 1.0, subtitle_text)],
            ExoSettings(font="ＭＳ Ｐゴシック"),
            total_duration=1.0,
            insert_initial_empty=False,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "subtitle.exo"
            write_exo(path, content)

            written = path.read_text(encoding="shift_jis")
            if sys.platform == "win32":
                self.assertIn(b"\r\n", path.read_bytes())

        self.assertEqual(written, content)
        self.assertIn(encode_text_for_exo(subtitle_text), written)

    def test_unsupported_font_reports_field_value_and_character(self) -> None:
        with self.assertRaises(ExoWriteError) as raised:
            generate_exo_file(
                [Subtitle(0.0, 1.0, "valid subtitle")],
                ExoSettings(font="Example Font 😀"),
                total_duration=1.0,
            )

        message = str(raised.exception)
        self.assertIn("exo.font", message)
        self.assertIn("Example Font", message)
        self.assertIn("😀", message)
        self.assertIn("Shift-JIS", message)

    def test_write_rejects_other_unencodable_literal_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.exo"
            with self.assertRaisesRegex(ExoWriteError, "unsupported character"):
                write_exo(path, "font=valid\ncustom=😀\n")

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
