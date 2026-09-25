import unittest

from subtitler.cleanup_gate import SAFE, cleanup_decision
from subtitler.models import Subtitle
from subtitler.subtitle_planner import _refine_subtitle_text
from subtitler.text_refiner import _parse_indexed_cleanup_response


class CleanupGateTests(unittest.TestCase):
    def test_safe_edits_and_protected_content(self):
        cases = [
            ("こんにちは世界", "こんにちは、世界。", True),
            ("StateofPlay", "State of Play", True),
            ("え、6月です。", "6月です。", True),
            ("えーっと、発表です。", "発表です。", True),
            ("えー、", "", True),
            ("発売しません", "発売します", False),
            ("E4インディー", "E3インディー", False),
            ("1.5倍です", "15倍です", False),
            ("6月3日です", "6月8日です", False),
            ("ローガンです", "モーガンです", False),
            ("日本語で話します", "I speak Japanese", False),
            ("楽しみです", "楽しみです。明日発売です", False),
            ("楽しみです", "え、楽しみです", False),
            ("そのままです", "まです", False),
            ("そのゲームです", "ゲームです", False),
            ("発売ですか?", "発売ですか。", False),
            ("メールです", "メルです", False),
            ("本当に本当に楽しい", "本当に楽しい", False),
            ("番組の番組の幕開け", "番組の幕開け", False),
            ("発売です", "", False),
            ("開発人です", "開発陣です", False),
            ("えらいですね", "らいですね", False),
        ]
        for before, after, expected in cases:
            with self.subTest(before=before, after=after):
                self.assertEqual(cleanup_decision(before, after) in SAFE, expected)

    def test_partial_filler_removal_is_deletion_only(self):
        self.assertIn(cleanup_decision("ま、発表は、え、明日", "発表は、え、明日"), SAFE)
        self.assertNotIn(cleanup_decision("ま、発表は、え、明日", "え、発表は、え、明日"), SAFE)

    def test_local_parser_retains_bad_edits_without_discarding_good_ones(self):
        rejected = []
        result, error = _parse_indexed_cleanup_response(
            "1\t発表です\n2\tE3です\n3\t<DELETE>\n4\t<DELETE>",
            ["え、発表です", "E4です", "重要な発言", "えーっと、"], rejected=rejected,
        )
        self.assertIsNone(error)
        self.assertEqual(result, ["発表です", "E4です", "重要な発言", ""])
        self.assertEqual(len(rejected), 2)

    def test_planner_applies_independent_edits_with_existing_filler_span_coverage(self):
        class Refiner:
            def refine(self, lines):
                return [{"え、発表です": "発表です", "E4です": "E3です",
                         "えーっと、": "", "最後です": "最後です。"}[line] for line in lines]

        for workers in (1, 2):
            with self.subTest(workers=workers):
                subtitles = [Subtitle(i, i + .9, text) for i, text in enumerate(
                    ["え、発表です", "E4です", "えーっと、", "最後です"])]
                stats = _refine_subtitle_text(subtitles, Refiner(), workers=workers, window_subtitles=2)
                self.assertEqual([s.text for s in subtitles], ["発表です", "E4です", "最後です。"])
                self.assertEqual([(s.start_time, s.end_time) for s in subtitles], [(0, .9), (1, 2.9), (3, 3.9)])
                self.assertEqual((stats.changed_count, stats.deleted_count), (2, 1))

    def test_bad_group_shape_cannot_shift_other_lines(self):
        class Refiner:
            def refine(self, lines):
                return ["発表です"]

        subtitles = [Subtitle(0, 1, "え、発表です"), Subtitle(1, 2, "重要です")]
        _refine_subtitle_text(subtitles, Refiner(), window_subtitles=2)
        self.assertEqual([s.text for s in subtitles], ["え、発表です", "重要です"])
