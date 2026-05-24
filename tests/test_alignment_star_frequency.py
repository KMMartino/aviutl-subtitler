import unittest


try:
    from ctc_forced_aligner.text_utils import preprocess_text
except ImportError:  # pragma: no cover - lets the test file import without optional deps
    preprocess_text = None


@unittest.skipIf(preprocess_text is None, "ctc_forced_aligner is not installed")
class AlignmentStarFrequencyTests(unittest.TestCase):
    def test_edges_mode_only_adds_edge_wildcards_for_japanese_char_alignment(self):
        tokens, text = preprocess_text(
            "どうも皆さん",
            romanize=True,
            language="ja",
            split_size="char",
            star_frequency="edges",
        )

        self.assertEqual(tokens[0], "<star>")
        self.assertEqual(tokens[-1], "<star>")
        self.assertEqual(text[0], "<star>")
        self.assertEqual(text[-1], "<star>")
        self.assertEqual(tokens.count("<star>"), 2)
        self.assertEqual(text.count("<star>"), 2)

    def test_segment_mode_documents_previous_per_character_wildcards(self):
        tokens, text = preprocess_text(
            "どうも皆さん",
            romanize=True,
            language="ja",
            split_size="char",
            star_frequency="segment",
        )

        self.assertGreater(tokens.count("<star>"), 2)
        self.assertGreater(text.count("<star>"), 2)


if __name__ == "__main__":
    unittest.main()
