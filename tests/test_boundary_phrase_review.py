import unittest
from contextlib import redirect_stdout
from io import StringIO

from subtitler.models import AlignedToken, Subtitle
from subtitler.subtitle_planner import _review_same_chain_leading_phrases
from subtitler.text_refiner import TextRefiner


def _tokens(text: str, start: float = 0.0) -> list[AlignedToken]:
    return [
        AlignedToken(text=char, start=start + index * 0.1, end=start + (index + 1) * 0.1, kind="char")
        for index, char in enumerate(text)
    ]


def _sub(text: str, start: float, chain: int, part: int) -> Subtitle:
    tokens = _tokens(text, start)
    return Subtitle(
        start_time=tokens[0].start,
        end_time=tokens[-1].end,
        text=text,
        tokens=tokens,
        chain_index=chain,
        chain_part_index=part,
        split_source="test",
    )


class BoundaryReviewRefiner(TextRefiner):
    def __init__(self, move: bool) -> None:
        self.move = move
        self.calls: list[tuple[str, str, str]] = []

    def should_move_leading_phrase_left(self, previous_text: str, current_text: str, phrase: str) -> bool:
        self.calls.append((previous_text, current_text, phrase))
        return self.move


class BoundaryPhraseReviewTests(unittest.TestCase):
    def test_moves_phrase_with_tokens_and_timing_inside_same_chain(self) -> None:
        subtitles = [
            _sub("前の話", 0.0, chain=1, part=0),
            _sub("で、続きです", 1.0, chain=1, part=1),
        ]

        with redirect_stdout(StringIO()):
            moved = _review_same_chain_leading_phrases(
                subtitles,
                BoundaryReviewRefiner(move=True),
                max_chars=20,
            )

        self.assertEqual(moved, 1)
        self.assertEqual(subtitles[0].text, "前の話で、")
        self.assertEqual(subtitles[1].text, "続きです")
        self.assertEqual([token.text for token in subtitles[0].tokens], list("前の話で、"))
        self.assertEqual([token.text for token in subtitles[1].tokens], list("続きです"))
        self.assertEqual(subtitles[0].end_time, 1.2)
        self.assertEqual(subtitles[1].start_time, 1.2)
        self.assertIn("llm_boundary_review", subtitles[0].timing_adjustment)

    def test_keeps_phrase_when_model_says_opener(self) -> None:
        subtitles = [
            _sub("前の話", 0.0, chain=1, part=0),
            _sub("で、次の話です", 1.0, chain=1, part=1),
        ]
        refiner = BoundaryReviewRefiner(move=False)

        moved = _review_same_chain_leading_phrases(subtitles, refiner, max_chars=20)

        self.assertEqual(moved, 0)
        self.assertEqual(subtitles[0].text, "前の話")
        self.assertEqual(subtitles[1].text, "で、次の話です")
        self.assertEqual(refiner.calls, [("前の話", "で、次の話です", "で、")])

    def test_does_not_cross_chain_boundaries(self) -> None:
        subtitles = [
            _sub("前の話", 0.0, chain=1, part=0),
            _sub("で、続きです", 1.0, chain=2, part=0),
        ]
        refiner = BoundaryReviewRefiner(move=True)

        moved = _review_same_chain_leading_phrases(subtitles, refiner, max_chars=20)

        self.assertEqual(moved, 0)
        self.assertEqual(refiner.calls, [])
        self.assertEqual(subtitles[1].text, "で、続きです")


if __name__ == "__main__":
    unittest.main()
