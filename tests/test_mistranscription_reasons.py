import unittest

from subtitler.text_refiner import _parse_mistranscription_flags


class MistranscriptionReasonTests(unittest.TestCase):
    def test_parses_optional_reason_column(self) -> None:
        flags = _parse_mistranscription_flags(
            "1\t変な単語\tproper noun mismatch",
            [(1, "これは変な単語です")],
        )
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].text, "変な単語")
        self.assertEqual(flags[0].reason, "proper noun mismatch")


if __name__ == "__main__":
    unittest.main()
