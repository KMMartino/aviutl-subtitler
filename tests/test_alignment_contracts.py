import unittest
from pathlib import Path
from unittest.mock import patch
from subtitler.aligner import ForcedAligner, ctc_language_code, is_japanese_language, proportional_alignment
from subtitler.errors import AlignmentError
from subtitler.models import AudioChunk, TranscriptChunk
import math
from subtitler.aligner import _precise_emission_stride_ms
from subtitler.alignment_pool import _split_transcript_for_subchunks


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

    def test_missing_ctc_aligner_fails_instead_of_falling_back(self):
        def fail_ctc_import(name, *args, **kwargs):
            if name == "ctc_forced_aligner":
                raise ModuleNotFoundError("No module named 'ctc_forced_aligner'", name="ctc_forced_aligner")
            return original_import(name, *args, **kwargs)

        original_import = __import__
        with patch("builtins.__import__", side_effect=fail_ctc_import):
            with self.assertRaisesRegex(AlignmentError, "ctc_forced_aligner is required"):
                ForcedAligner(
                    model_name="unused",
                    language="eng",
                    device="cpu",
                    split_size="word",
                    temp_dir=Path("."),
                    sample_rate=16000,
                )


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


class FakeSizedTensor:
    def __init__(self, size: int) -> None:
        self._size = size

    def size(self, dimension: int) -> int:
        self.assert_dimension_zero(dimension)
        return self._size

    @staticmethod
    def assert_dimension_zero(dimension: int) -> None:
        if dimension != 0:
            raise AssertionError(f"unexpected dimension: {dimension}")


class AlignmentStrideTests(unittest.TestCase):
    def test_precise_emission_stride_uses_actual_frame_count(self) -> None:
        audio_waveform = FakeSizedTensor(16000 * 30 + 1)
        emissions = FakeSizedTensor(1500)

        stride = _precise_emission_stride_ms(audio_waveform, emissions)

        expected = float(16000 * 30 + 1) * 1000.0 / 1500.0 / 16000.0
        self.assertEqual(stride, expected)
        self.assertNotEqual(stride, math.ceil(stride))


class AlignmentRetrySplitTests(unittest.TestCase):
    def test_japanese_transcript_is_partitioned_across_subchunk_durations(self):
        samples = [0.0] * 16000
        parent = AudioChunk(index=5, start=10.0, end=14.0, samples=samples)
        subchunks = [
            AudioChunk(index=5, start=10.0, end=11.0, samples=samples[:4000]),
            AudioChunk(index=5, start=11.0, end=14.0, samples=samples[4000:]),
        ]

        transcripts = _split_transcript_for_subchunks(
            TranscriptChunk(parent, "あいうえおかきく"),
            subchunks,
            "ja",
        )

        self.assertEqual([item.text for item in transcripts], ["あい", "うえおかきく"])
        self.assertEqual([item.chunk.start for item in transcripts], [10.0, 11.0])
        self.assertEqual([item.chunk.end for item in transcripts], [11.0, 14.0])
