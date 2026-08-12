import unittest

from subtitler.models import AlignedToken, SplitPlanResult
from subtitler.splitter import _boundary_options, _is_legal_boundary, split_token_chain


def _tokens(text: str) -> list[AlignedToken]:
    return [
        AlignedToken(text=char, start=index * 0.1, end=(index + 1) * 0.1, kind="char")
        for index, char in enumerate(text)
    ]


class FakeSplitPlanner:
    def supports_multi_split(self) -> bool:
        return False

    def split_input_capacity(self, max_chars: int) -> int:
        return 120

    def select_split_boundaries(
        self, text: str, annotated_text: str, candidate_ids: list[str], max_chars: int, *, multiple: bool = False
    ) -> SplitPlanResult:
        return SplitPlanResult(selected_ids=[candidate_ids[0]], candidate_ids=candidate_ids, accepted=True)


class TitleBoundaryTests(unittest.TestCase):
    def test_ascii_title_run_boundary_is_illegal(self) -> None:
        tokens = _tokens("SummerGameFestがあります")

        self.assertFalse(_is_legal_boundary(tokens, 6))

    def test_middle_dot_title_run_boundary_is_illegal(self) -> None:
        tokens = _tokens("ゴッド・オブ・ウォーです")

        self.assertFalse(_is_legal_boundary(tokens, 5))

    def test_long_katakana_title_run_boundary_is_illegal(self) -> None:
        tokens = _tokens("アクセシビリティサマーショーケースです")

        self.assertFalse(_is_legal_boundary(tokens, 8))

    def test_deterministic_split_prefers_list_separator_after_title(self) -> None:
        subtitles = split_token_chain(
            _tokens("ゴッド・オブ・ウォー、次のタイトルもあります"),
            max_chars=12,
            max_duration=6.0,
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertEqual(subtitles[0].text, "ゴッド・オブ・ウォー、")

    def test_llm_split_inside_title_cluster_is_rejected(self) -> None:
        options = _boundary_options(_tokens("SummerGameFest、次の話です"), 12, 6.0, multiple=False)

        self.assertNotIn(6, [option.index for option in options])

    def test_fallback_deterministic_split_still_returns_valid_subtitles(self) -> None:
        subtitles = split_token_chain(
            _tokens("SummerGameFest、次の話ですさらに続きます"),
            max_chars=12,
            max_duration=6.0,
            llm_splitter=FakeSplitPlanner(),
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertTrue(all(sub.text for sub in subtitles))
        self.assertNotEqual(subtitles[0].text, "Summer")


if __name__ == "__main__":
    unittest.main()
