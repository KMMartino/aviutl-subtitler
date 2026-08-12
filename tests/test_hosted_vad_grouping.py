import tempfile
import unittest
from pathlib import Path

import numpy as np

from subtitler.backends.existing_pipeline import (
    build_hosted_transcription_chunks,
    capped_align_workers,
    deduplicate_overlapping_aligned_chunks,
    uses_larger_hosted_transcription_segments,
)
from subtitler.models import AlignedChunk, AlignedToken, AudioChunk


class HostedVadGroupingTests(unittest.TestCase):
    def test_consecutive_fine_chunks_are_materialized_as_continuous_groups(self) -> None:
        chunks = [
            AudioChunk(index=0, start=0.0, end=2.0, samples=[]),
            AudioChunk(index=1, start=2.5, end=4.0, samples=[]),
            AudioChunk(index=2, start=8.0, end=10.0, samples=[]),
        ]
        samples = np.zeros(1000, dtype=np.float32)

        with tempfile.TemporaryDirectory() as temp_name:
            groups = build_hosted_transcription_chunks(
                all_chunks=chunks,
                selected_chunks=chunks,
                samples=samples,
                sample_rate=100,
                max_group_sec=6.0,
                temp_dir=Path(temp_name),
            )

            self.assertEqual([(group.start, group.end) for group in groups], [(0.0, 4.0), (8.0, 10.0)])
            self.assertEqual([len(group.samples) for group in groups], [400, 200])
            self.assertTrue(all(group.wav_path and group.wav_path.is_file() for group in groups))

    def test_groups_do_not_bridge_unselected_fine_chunks(self) -> None:
        chunks = [
            AudioChunk(index=0, start=0.0, end=2.0, samples=[]),
            AudioChunk(index=1, start=2.0, end=4.0, samples=[]),
            AudioChunk(index=2, start=4.0, end=6.0, samples=[]),
        ]
        samples = np.zeros(600, dtype=np.float32)

        with tempfile.TemporaryDirectory() as temp_name:
            groups = build_hosted_transcription_chunks(
                all_chunks=chunks,
                selected_chunks=[chunks[0], chunks[2]],
                samples=samples,
                sample_rate=100,
                max_group_sec=60.0,
                temp_dir=Path(temp_name),
            )

        self.assertEqual([(group.start, group.end) for group in groups], [(0.0, 2.0), (4.0, 6.0)])

    def test_aligner_workers_are_capped_by_transcription_segment_count(self) -> None:
        self.assertEqual(capped_align_workers(8, 3), 3)
        self.assertEqual(capped_align_workers(2, 3), 2)
        self.assertEqual(capped_align_workers(8, 0), 0)

    def test_larger_groups_are_scoped_to_gpt_transcribe_adapter(self) -> None:
        self.assertTrue(
            uses_larger_hosted_transcription_segments(
                {
                    "backend": {
                        "transcriber": "openai",
                        "transcription_model": "gpt-transcribe",
                    }
                }
            )
        )

    def test_overlap_deduplication_uses_text_and_global_time(self) -> None:
        left_chunk = AudioChunk(index=0, start=0.0, end=10.0, samples=[])
        right_chunk = AudioChunk(index=1, start=9.0, end=20.0, samples=[])
        left = AlignedChunk(
            chunk=left_chunk,
            text="前半重複部分",
            tokens=[
                AlignedToken(char, 8.0 + index * 0.2, 8.2 + index * 0.2, "char")
                for index, char in enumerate("前半重複部分")
            ],
        )
        right = AlignedChunk(
            chunk=right_chunk,
            text="重複部分後半",
            tokens=[
                AlignedToken(char, 9.0 + index * 0.2, 9.2 + index * 0.2, "char")
                for index, char in enumerate("重複部分後半")
            ],
        )

        result = deduplicate_overlapping_aligned_chunks([left, right])

        self.assertEqual("".join(item.text for item in result), "前半重複部分後半")
        self.assertFalse(
            uses_larger_hosted_transcription_segments(
                {
                    "backend": {
                        "transcriber": "gemini",
                        "transcription_model": "gemini-3.5-flash",
                    }
                }
            )
        )

    def test_overlap_deduplication_falls_back_to_aligned_seam_for_different_text(self) -> None:
        left = AlignedChunk(
            chunk=AudioChunk(index=0, start=0.0, end=10.0, samples=[]),
            text="前半ところ",
            tokens=[
                AlignedToken(char, 8.0 + index * 0.3, 8.3 + index * 0.3, "char")
                for index, char in enumerate("前半ところ")
            ],
        )
        right = AlignedChunk(
            chunk=AudioChunk(index=1, start=9.5, end=20.0, samples=[]),
            text="頃の告知後半",
            tokens=[
                AlignedToken(char, 9.5 + index * 0.3, 9.8 + index * 0.3, "char")
                for index, char in enumerate("頃の告知後半")
            ],
        )

        result = deduplicate_overlapping_aligned_chunks([left, right])

        self.assertEqual(len(result), 2)
        self.assertEqual("".join(item.text for item in result), "前半ところの告知後半")
        self.assertTrue(all((token.start + token.end) / 2.0 <= 9.75 for token in result[0].tokens))
        self.assertTrue(all((token.start + token.end) / 2.0 > 9.75 for token in result[1].tokens))

    def test_overlap_deduplication_moves_seam_to_shifted_shared_phrase(self) -> None:
        def token(char: str, midpoint: float) -> AlignedToken:
            return AlignedToken(char, midpoint - 0.05, midpoint + 0.05, "char")

        left = AlignedChunk(
            chunk=AudioChunk(index=0, start=0.0, end=12.0, samples=[]),
            text="前半共有部分余分",
            tokens=[
                token("前", 8.1), token("半", 8.3),
                token("共", 9.0), token("有", 9.2), token("部", 9.4), token("分", 9.6),
                token("余", 10.5), token("分", 10.7),
            ],
        )
        right = AlignedChunk(
            chunk=AudioChunk(index=1, start=8.0, end=20.0, samples=[]),
            text="先頭共有部分後半",
            tokens=[
                token("先", 8.2), token("頭", 8.4),
                token("共", 9.05), token("有", 9.25), token("部", 9.45), token("分", 9.65),
                token("後", 10.6), token("半", 10.8),
            ],
        )

        result = deduplicate_overlapping_aligned_chunks([left, right])

        self.assertEqual("".join(item.text for item in result), "前半共有部分後半")
        self.assertLess((result[0].tokens[-1].start + result[0].tokens[-1].end) / 2.0, 10.0)


if __name__ == "__main__":
    unittest.main()
