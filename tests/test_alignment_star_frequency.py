import unittest

from subtitler.aligner import ctc_language_code, is_japanese_language, proportional_alignment
from subtitler.models import AudioChunk, TranscriptChunk


try:
    from ctc_forced_aligner.text_utils import preprocess_text
except ImportError:  # pragma: no cover - lets the test file import without optional deps
    preprocess_text = None


class AlignmentLanguageTests(unittest.TestCase):
    def test_japanese_app_language_maps_to_ctc_jpn(self):
        self.assertEqual(ctc_language_code("ja"), "jpn")
        self.assertEqual(ctc_language_code("jp"), "jpn")
        self.assertEqual(ctc_language_code("jpn"), "jpn")
        self.assertEqual(ctc_language_code("eng"), "eng")

    def test_ja_and_jpn_use_character_fallback(self):
        chunk = AudioChunk(index=1, start=0.0, end=3.0, samples=[])

        for language in ("ja", "jpn"):
            aligned = proportional_alignment(TranscriptChunk(chunk, "あいう"), language)
            self.assertTrue(is_japanese_language(language))
            self.assertEqual([token.text for token in aligned.tokens], ["あ", "い", "う"])
            self.assertTrue(all(token.kind == "char" for token in aligned.tokens))


@unittest.skipIf(preprocess_text is None, "ctc_forced_aligner is not installed")
class AlignmentStarFrequencyTests(unittest.TestCase):
    def test_edges_mode_only_adds_edge_wildcards_for_japanese_char_alignment(self):
        tokens, text = preprocess_text(
            "どうも皆さん",
            romanize=True,
            language="jpn",
            split_size="char",
            star_frequency="edges",
        )

        self.assertEqual(tokens[0], "<star>")
        self.assertEqual(tokens[-1], "<star>")
        self.assertEqual(text[0], "<star>")
        self.assertEqual(text[-1], "<star>")
        self.assertEqual(tokens.count("<star>"), 2)
        self.assertEqual(text.count("<star>"), 2)

if __name__ == "__main__":
    unittest.main()
