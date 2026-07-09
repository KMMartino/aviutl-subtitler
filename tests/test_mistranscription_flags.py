import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from aviutl_subtitle import _flag_possible_mistranscriptions
from subtitler.models import MisTranscriptionFlag, Subtitle
from subtitler.text_refiner import (
    LlamaServerTextRefiner,
    _deterministic_mistranscription_flags,
    _parse_mistranscription_flags,
)


class MistranscriptionFlagParserTests(unittest.TestCase):
    def test_ignores_stray_none_without_discarding_later_flags(self) -> None:
        flags = _parse_mistranscription_flags(
            "NONE\n2\thigh\tスーパーマリオブラザーズワンダーランド\tbroken title\n",
            [
                (1, "今日は新作情報です"),
                (2, "スーパーマリオブラザーズワンダーランドの発売日です"),
            ],
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].line_number, 2)
        self.assertEqual(flags[0].text, "スーパーマリオブラザーズワンダーランド")
        self.assertEqual(flags[0].severity, "high")

    def test_accepts_old_three_column_flag_format(self) -> None:
        flags = _parse_mistranscription_flags(
            "1\tラスアスパート3\tbroken product name\n",
            [(1, "ラスアスパート3が出るかもしれない")],
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].text, "ラスアスパート3")
        self.assertEqual(flags[0].severity, "medium")

    def test_unknown_severity_is_coerced_to_medium(self) -> None:
        flags = _parse_mistranscription_flags(
            "1\turgent\tラスアスパート3\tbroken\tproduct name\n",
            [(1, "ラスアスパート3が出るかもしれない")],
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].text, "ラスアスパート3")
        self.assertEqual(flags[0].severity, "medium")

    def test_rejects_non_copied_flag_text(self) -> None:
        flags = _parse_mistranscription_flags(
            "1\t修正された言葉\n",
            [(1, "元の字幕テキストです")],
        )

        self.assertEqual(flags, [])

    def test_deterministically_flags_glossary_leak(self) -> None:
        flags = _deterministic_mistranscription_flags(
            [
                (38, "RDNA2を搭載しています"),
                (39, "PSSR | prefer over PSVR"),
            ]
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].line_number, 39)
        self.assertEqual(flags[0].text, "PSSR | prefer over PSVR")
        self.assertEqual(flags[0].severity, "high")

    def test_deterministically_flags_mojibake(self) -> None:
        flags = _deterministic_mistranscription_flags(
            [(105, "‚ЕЃA–і‰ї’l‚И‚а‚М")]
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].line_number, 105)
        self.assertEqual(flags[0].severity, "high")

    def test_deterministically_flags_repeated_fragment(self) -> None:
        flags = _deterministic_mistranscription_flags(
            [(7, "これはテストテストです")]
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].text, "テストテスト")
        self.assertEqual(flags[0].severity, "high")

    def test_final_review_logs_progress(self) -> None:
        refiner = object.__new__(LlamaServerTextRefiner)
        refiner.last_mistranscription_raw = ""
        refiner._chat = lambda prompt, max_tokens=512: "NONE"
        numbered = [(index, f"字幕{index}") for index in range(1, 18)]

        output = StringIO()
        with redirect_stdout(output):
            flags = refiner.flag_mistranscriptions(numbered)

        self.assertEqual(flags, [])
        log = output.getvalue()
        self.assertIn("Final candidate review: 17 subtitles in 2 batch(es).", log)
        self.assertIn("Final candidate review batch 1/2", log)
        self.assertIn("Final candidate review batch 2/2", log)
        self.assertIn("Final candidate review complete: 0 unique candidate(s).", log)

    def test_sidecar_includes_severity_and_low_flags_do_not_create_markers(self) -> None:
        class Refiner:
            last_mistranscription_raw = "raw"

            def flag_mistranscriptions(self, numbered_lines):
                return [
                    MisTranscriptionFlag(1, "弱い候補", "minor issue", "low"),
                    MisTranscriptionFlag(2, "強い候補", "clear issue", "high"),
                ]

        subtitles = [
            Subtitle(0.0, 1.0, "弱い候補です"),
            Subtitle(1.0, 2.0, "強い候補です"),
        ]
        with TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "flags.txt"
            markers = _flag_possible_mistranscriptions(subtitles, Refiner(), path)
            text = path.read_text(encoding="utf-8")

        self.assertIn("1\tlow\t弱い候補\tminor issue", text)
        self.assertIn("2\thigh\t強い候補\tclear issue", text)
        self.assertEqual(len(markers), 1)
        self.assertIn("high - clear issue", markers[0].text)


if __name__ == "__main__":
    unittest.main()
