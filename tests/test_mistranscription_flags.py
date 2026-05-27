import unittest
from contextlib import redirect_stdout
from io import StringIO

from subtitler.text_refiner import (
    LlamaServerTextRefiner,
    _deterministic_mistranscription_flags,
    _parse_mistranscription_flags,
)


class MistranscriptionFlagParserTests(unittest.TestCase):
    def test_ignores_stray_none_without_discarding_later_flags(self) -> None:
        flags = _parse_mistranscription_flags(
            "NONE\n2\tスーパーマリオブラザーズワンダーランド\n",
            [
                (1, "今日は新作情報です"),
                (2, "スーパーマリオブラザーズワンダーランドの発売日です"),
            ],
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].line_number, 2)
        self.assertEqual(flags[0].text, "スーパーマリオブラザーズワンダーランド")

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

    def test_deterministically_flags_mojibake(self) -> None:
        flags = _deterministic_mistranscription_flags(
            [(105, "‚ЕЃA–і‰ї’l‚И‚а‚М")]
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].line_number, 105)

    def test_deterministically_flags_repeated_fragment(self) -> None:
        flags = _deterministic_mistranscription_flags(
            [(7, "これはテストテストです")]
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].text, "テストテスト")

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


if __name__ == "__main__":
    unittest.main()
