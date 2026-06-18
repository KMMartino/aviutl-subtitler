import unittest

from subtitler.models import AlignedToken
from subtitler.splitter import (
    TokenSegment,
    _assert_or_repair_connective_heads,
    _is_legal_boundary,
    _llm_boundary_candidate,
    _tokens_to_text,
    split_token_chain,
)


def _tokens(text: str) -> list[AlignedToken]:
    return [
        AlignedToken(text=char, start=index * 0.1, end=(index + 1) * 0.1, kind="char")
        for index, char in enumerate(text)
    ]


class FakeSplitPlanner:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.calls = 0

    def split_lines(self, text: str, max_chars: int) -> list[str]:
        self.calls += 1
        return self.lines


class ConnectiveBoundaryTests(unittest.TestCase):
    def test_boundary_before_mo_phrase_is_illegal(self) -> None:
        tokens = _tokens("前の話も、続きです")

        self.assertFalse(_is_legal_boundary(tokens, 3))

    def test_boundary_after_mo_phrase_is_legal(self) -> None:
        tokens = _tokens("前の話も、続きです")

        self.assertTrue(_is_legal_boundary(tokens, 5))

    def test_deterministic_split_places_mo_phrase_on_previous_tail(self) -> None:
        subtitles = split_token_chain(
            _tokens("前の話も、続きですさらに続く"),
            max_chars=8,
            max_duration=6.0,
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertEqual(subtitles[0].text, "前の話も、")
        self.assertFalse(subtitles[1].text.startswith("も、"))

    def test_llm_boundary_before_mo_phrase_is_repaired(self) -> None:
        segment = TokenSegment(_tokens("前の話も、続きです"), "initial")
        candidate = _llm_boundary_candidate(
            segment,
            max_chars=8,
            llm_splitter=FakeSplitPlanner(["前の話", "も、続きです"]),
            llm_split_callback=None,
            attempt_index=1,
            pass_name="llm_boundary",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.index, 5)
        self.assertEqual(candidate.kind, "llm_boundary_repaired")

    def test_hard_max_split_avoids_mo_phrase_head_when_it_fits(self) -> None:
        subtitles = split_token_chain(
            _tokens("あいうも、えおかきくけこ"),
            max_chars=6,
            max_duration=6.0,
        )

        self.assertEqual(subtitles[0].text, "あいうも、")
        self.assertFalse(subtitles[1].text.startswith("も、"))

    def test_unrepairable_connective_head_is_marked(self) -> None:
        segments = [
            TokenSegment(_tokens("あいうえ"), "left"),
            TokenSegment(_tokens("も、続き"), "right"),
        ]

        repaired = _assert_or_repair_connective_heads(segments, max_chars=4)

        self.assertEqual([_tokens_to_text(segment.tokens) for segment in repaired], ["あいうえ", "も、続き"])
        self.assertIn("connective_head_unrepaired", repaired[1].source)

    def test_subtitle_text_and_timing_match_token_slice(self) -> None:
        subtitles = split_token_chain(
            _tokens("前の話も、続きですさらに続く"),
            max_chars=8,
            max_duration=6.0,
        )

        for subtitle in subtitles:
            self.assertEqual(subtitle.text, "".join(token.text for token in subtitle.tokens))
            self.assertEqual(subtitle.start_time, subtitle.tokens[0].start)
            self.assertEqual(subtitle.end_time, subtitle.tokens[-1].end)

    def test_deterministic_structural_boundary_does_not_call_llm(self) -> None:
        planner = FakeSplitPlanner(["前の話", "も、続きです"])

        subtitles = split_token_chain(
            _tokens("前の話も、続きです"),
            max_chars=8,
            max_duration=6.0,
            llm_splitter=planner,
        )

        self.assertEqual(planner.calls, 0)
        self.assertEqual(subtitles[0].text, "前の話も、")

    def test_sentence_end_split_marks_left_side(self) -> None:
        subtitles = split_token_chain(
            _tokens("これは文です。次の文です"),
            max_chars=8,
            max_duration=6.0,
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertTrue(subtitles[0].text.endswith("。"))
        self.assertIn("sentence_terminal", subtitles[0].split_source)
        self.assertNotIn("sentence_terminal", subtitles[1].split_source)


if __name__ == "__main__":
    unittest.main()
