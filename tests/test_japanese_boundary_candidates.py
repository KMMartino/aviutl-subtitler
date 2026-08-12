import unittest

from subtitler.models import AlignedToken
from subtitler.splitter import (
    _boundary_options,
    _inside_protected_numeric_expression,
    _is_legal_boundary,
    _semantic_boundary_penalty,
)


def _tokens(text: str) -> list[AlignedToken]:
    return [
        AlignedToken(char, index * 0.1, (index + 1) * 0.1, "char")
        for index, char in enumerate(text)
    ]


class JapaneseBoundaryCandidateTests(unittest.TestCase):
    def test_never_offers_boundary_inside_katakana_run(self) -> None:
        text = "短いゲームです"

        self.assertFalse(_is_legal_boundary(_tokens(text), text.index("ム")))

    def test_never_offers_boundary_inside_kanji_run(self) -> None:
        text = "音声認識の結果です"

        self.assertFalse(_is_legal_boundary(_tokens(text), text.index("認")))

    def test_hiragana_to_content_transition_beats_hiragana_run(self) -> None:
        text = "必要であれば通訳付きで配信します"
        tokens = _tokens(text)
        natural = text.index("通")
        broken = text.index("れ")

        self.assertLess(
            _semantic_boundary_penalty(tokens, natural),
            _semantic_boundary_penalty(tokens, broken),
        )

    def test_candidate_lattice_avoids_three_observed_bad_seams(self) -> None:
        cases = [
            (
                "そちらの2つは多分このチャンネルでも必要であれば通訳付きで配信をしようと思いますので、",
                "必要であ|れば",
                "必要であれば|通訳",
            ),
            (
                "特にインディー系のゲームがサマーゲームフェスに合わせていろいろと立ち並んでいるという感じになりますので、",
                "いろい|ろ",
                "いろいろと|立ち",
            ),
            (
                "特にサマーゲームフェスとStateofPlayについてはうちのチャンネルでも配信しますので、",
                "うち|の",
                "については|うち",
            ),
        ]
        for text, bad, good in cases:
            with self.subTest(text=text):
                offered = {
                    f"{text[:option.index]}|{text[option.index:]}"
                    for option in _boundary_options(_tokens(text), 40, 6.0, multiple=True)
                }
                self.assertFalse(any(bad in candidate for candidate in offered))
                self.assertTrue(any(good in candidate for candidate in offered))
                if "サマーゲームフェス" in text:
                    self.assertFalse(any("フェスに|合わせ" in candidate for candidate in offered))

    def test_date_and_time_expression_is_atomic(self) -> None:
        text = "6月3日水曜日午前6時から放送"
        expression_end = text.index("から")

        self.assertTrue(
            all(not _is_legal_boundary(_tokens(text), index) for index in range(1, expression_end))
        )
        self.assertTrue(_is_legal_boundary(_tokens(text), expression_end))

    def test_compact_numeric_expressions_are_atomic(self) -> None:
        cases = [
            ("12時30分に開始", "12時30分"),
            ("12:30に開始", "12:30"),
            ("2026/08/02に開始", "2026/08/02"),
            ("2026-08-02に開始", "2026-08-02"),
            ("第3回イベント", "第3回"),
            ("2、30分ぐらい", "2、30分"),
            ("20〜30分です", "20〜30分"),
            ("約3.5秒です", "約3.5秒"),
            ("16GBモデル", "16GB"),
        ]
        for text, expression in cases:
            with self.subTest(text=text):
                start = text.index(expression)
                end = start + len(expression)
                self.assertTrue(
                    all(
                        _inside_protected_numeric_expression(text, index)
                        for index in range(start + 1, end)
                    )
                )

    def test_auxiliary_phrase_is_atomic(self) -> None:
        text = "必要であれば通訳付きで配信します"
        expression = "であれば"
        start = text.index(expression)
        end = start + len(expression)

        self.assertTrue(
            all(not _is_legal_boundary(_tokens(text), index) for index in range(start + 1, end))
        )

    def test_numeric_expression_can_split_after_following_particle(self) -> None:
        text = "日本時間6月3日水曜日午前6時から放送します"
        tokens = _tokens(text)
        after_time = text.index("から")
        after_particle = text.index("放送")

        self.assertGreater(
            _semantic_boundary_penalty(tokens, after_time),
            _semantic_boundary_penalty(tokens, after_particle),
        )


if __name__ == "__main__":
    unittest.main()
