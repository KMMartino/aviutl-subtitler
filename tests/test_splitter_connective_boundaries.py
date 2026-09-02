import unittest

from subtitler.models import AlignedToken, SplitPlanResult
from subtitler.splitter import (
    TokenSegment,
    _assert_or_repair_connective_heads,
    _boundary_options,
    _is_legal_boundary,
    _tokens_to_text,
    split_token_chain,
)


def _tokens(text: str) -> list[AlignedToken]:
    return [
        AlignedToken(text=char, start=index * 0.1, end=(index + 1) * 0.1, kind="char")
        for index, char in enumerate(text)
    ]


class FakeSplitPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def supports_multi_split(self) -> bool:
        return False

    def split_input_capacity(self, max_chars: int) -> int:
        return 120

    def select_split_boundaries(
        self, text: str, annotated_text: str, candidate_ids: list[str], max_chars: int, *, multiple: bool = False
    ) -> SplitPlanResult:
        self.calls += 1
        return SplitPlanResult(selected_ids=[candidate_ids[-1]], candidate_ids=candidate_ids, accepted=True)


class ConnectiveBoundaryTests(unittest.TestCase):
    def test_boundary_before_mo_phrase_is_illegal(self) -> None:
        tokens = _tokens("前の話も、続きです")

        self.assertFalse(_is_legal_boundary(tokens, 3))

    def test_boundary_after_mo_phrase_is_legal(self) -> None:
        tokens = _tokens("前の話も、続きです")

        self.assertTrue(_is_legal_boundary(tokens, 5))

    def test_boundary_inside_desu_kedomo_is_illegal(self) -> None:
        tokens = _tokens("短い前置きですけども次の話です")

        self.assertFalse(_is_legal_boundary(tokens, len("短い前置きです")))
        self.assertTrue(_is_legal_boundary(tokens, len("短い前置きですけども")))

    def test_boundary_inside_masu_question_is_illegal(self) -> None:
        tokens = _tokens("ここで終わりますか、次の話です")

        self.assertFalse(_is_legal_boundary(tokens, len("ここで終わります")))
        self.assertTrue(_is_legal_boundary(tokens, len("ここで終わりますか、")))

    def test_boundary_before_bare_kedomo_is_illegal(self) -> None:
        tokens = _tokens("前の話けども次の話です")

        self.assertFalse(_is_legal_boundary(tokens, len("前の話")))

    def test_deterministic_split_places_mo_phrase_on_previous_tail(self) -> None:
        subtitles = split_token_chain(
            _tokens("前の話も、続きですさらに続く"),
            max_chars=8,
            max_duration=6.0,
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertEqual(subtitles[0].text, "前の話も、")
        self.assertFalse(subtitles[1].text.startswith("も、"))

    def test_llm_candidates_never_offer_boundary_before_mo_phrase(self) -> None:
        options = _boundary_options(_tokens("前の話も、続きですさらに続く"), 8, 6.0, multiple=False)

        self.assertTrue(options)
        self.assertNotIn(3, [option.index for option in options])

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

    def test_bare_kedomo_head_moves_to_previous_subtitle(self) -> None:
        segments = [
            TokenSegment(_tokens("前の話"), "left"),
            TokenSegment(_tokens("けども次の話"), "right"),
        ]

        repaired = _assert_or_repair_connective_heads(segments, max_chars=20)

        self.assertEqual(
            [_tokens_to_text(segment.tokens) for segment in repaired],
            ["前の話けども", "次の話"],
        )

    def test_question_particle_head_moves_to_previous_subtitle(self) -> None:
        segments = [
            TokenSegment(_tokens("ここで終わります"), "left"),
            TokenSegment(_tokens("か、次の話"), "right"),
        ]

        repaired = _assert_or_repair_connective_heads(segments, max_chars=20)

        self.assertEqual(
            [_tokens_to_text(segment.tokens) for segment in repaired],
            ["ここで終わりますか、", "次の話"],
        )

    def test_over_limit_kedomo_boundary_does_not_strand_clause_ending(self) -> None:
        subtitles = split_token_chain(
            _tokens("あ" * 37 + "ですけども" + "次の話を詳しく説明する内容" * 4),
            max_chars=40,
            max_duration=6.0,
        )

        self.assertFalse(any(subtitle.text == "ですけども" for subtitle in subtitles))
        self.assertFalse(any(subtitle.text == "けども" for subtitle in subtitles))
        continued = next(subtitle.text for subtitle in subtitles if subtitle.text.startswith("ですけども"))
        self.assertGreater(len(continued), len("ですけども"))

    def test_over_limit_question_boundary_does_not_strand_particle(self) -> None:
        subtitles = split_token_chain(
            _tokens("あ" * 37 + "ますか、" + "次の話を詳しく説明する内容" * 4),
            max_chars=40,
            max_duration=6.0,
        )

        self.assertFalse(any(subtitle.text in {"か", "か、", "、"} for subtitle in subtitles))
        continued = next(subtitle.text for subtitle in subtitles if subtitle.text.startswith("ますか、"))
        self.assertGreater(len(continued), len("ますか、"))

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
        planner = FakeSplitPlanner()

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
