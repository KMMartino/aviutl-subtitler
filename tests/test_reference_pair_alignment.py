import unittest

from tools.reference_pair_alignment import align_transcripts


def _row(start: int, text: str) -> dict:
    return {"start_ms": start, "end_ms": start + 2000, "text": text}


class ReferencePairAlignmentTests(unittest.TestCase):
    def test_aligns_retained_speech_and_leaves_narration_unmatched(self):
        finished = [
            _row(0, "This challenge only lets me use one hand"),
            _row(5000, "open the red door and run away quickly"),
            _row(8000, "that monster nearly caught me in the hall"),
        ]
        vod = [
            _row(120_000, "open the red door and run away quickly"),
            _row(123_000, "that monster nearly caught me in the hall"),
        ]
        result = align_transcripts(finished, {"vod": vod})
        self.assertEqual(result["matched_utterances"], 2)
        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["vod_start_ms"], 120_000)

    def test_rejects_weak_common_word_overlap(self):
        result = align_transcripts(
            [_row(0, "this is the thing that we have")],
            {"vod": [_row(1000, "this is not at all the same statement")]},
        )
        self.assertEqual(result["matched_utterances"], 0)


if __name__ == "__main__":
    unittest.main()
