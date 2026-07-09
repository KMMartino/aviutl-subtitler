import unittest

from subtitler.models import AlignedToken
from subtitler.splitter import TokenSegment, _is_legal_boundary, _llm_boundary_candidate, split_token_chain


def _tokens(text: str) -> list[AlignedToken]:
    return [
        AlignedToken(text=char, start=index * 0.1, end=(index + 1) * 0.1, kind="char")
        for index, char in enumerate(text)
    ]


class FakeSplitPlanner:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def split_lines(self, text: str, max_chars: int) -> list[str]:
        return self.lines


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
        result = []
        segment = TokenSegment(_tokens("SummerGameFest、次の話です"), "initial")
        candidate = _llm_boundary_candidate(
            segment,
            max_chars=12,
            llm_splitter=FakeSplitPlanner(["Summer", "GameFest、次の話です"]),
            llm_split_callback=lambda item, *_: result.append(item),
            attempt_index=1,
            pass_name="llm_boundary",
        )

        self.assertIsNone(candidate)
        self.assertEqual(result[0].reject_reason, "title_cluster_split")

    def test_fallback_deterministic_split_still_returns_valid_subtitles(self) -> None:
        subtitles = split_token_chain(
            _tokens("SummerGameFest、次の話ですさらに続きます"),
            max_chars=12,
            max_duration=6.0,
            llm_splitter=FakeSplitPlanner(["Summer", "GameFest、次の話ですさらに続きます"]),
        )

        self.assertGreaterEqual(len(subtitles), 2)
        self.assertTrue(all(sub.text for sub in subtitles))
        self.assertNotEqual(subtitles[0].text, "Summer")


if __name__ == "__main__":
    unittest.main()
