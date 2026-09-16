import json
import unittest
from unittest.mock import patch

from subtitler.api_usage import ApiUsageLedger

from subtitler.external_refiners import HostedTextRefiner, parse_youtube_chapter_response


SUBTITLES = [
    (1, 0.0, 1.0, "intro"),
    (2, 1.0, 2.0, "history"),
    (3, 2.0, 3.0, "current"),
    (4, 3.0, 4.0, "future"),
]


class YouTubeChapterParserTests(unittest.TestCase):
    def test_valid_json_produces_ordered_chapters(self):
        raw = json.dumps(
            {
                "chapters": [
                    {"start_line": 1, "end_line": 2, "title": "Intro & History"},
                    {"start_line": 3, "end_line": 4, "title": "Present & Future"},
                ],
                "cuts": [
                    {"after_line": 2, "previous_topic": "Intro", "next_topic": "Current"}
                ],
            }
        )

        chapters, cuts = parse_youtube_chapter_response(raw, SUBTITLES)

        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].start_subtitle_index, 1)
        self.assertEqual(chapters[0].end_subtitle_index, 2)
        self.assertEqual(chapters[0].title, "Intro & History")
        self.assertEqual(chapters[0].previous_topic, "Intro")
        self.assertEqual(chapters[0].next_topic, "Current")
        self.assertEqual(cuts[0]["after_line"], 2)

    def test_malformed_json_returns_no_chapters(self):
        chapters, cuts = parse_youtube_chapter_response("not json", SUBTITLES)

        self.assertEqual(chapters, [])
        self.assertEqual(cuts, [])

    def test_overlapping_spans_are_rejected(self):
        raw = json.dumps(
            {
                "chapters": [
                    {"start_line": 1, "end_line": 3, "title": "First"},
                    {"start_line": 3, "end_line": 4, "title": "Overlap"},
                ]
            }
        )

        chapters, _ = parse_youtube_chapter_response(raw, SUBTITLES)

        self.assertEqual(chapters, [])

    def test_title_fallback_for_empty_titles(self):
        raw = json.dumps(
            {
                "chapters": [
                    {"start_line": 1, "end_line": 2, "title": ""},
                    {"start_line": 3, "end_line": 4, "title": "End"},
                ]
            }
        )

        chapters, _ = parse_youtube_chapter_response(raw, SUBTITLES)

        self.assertEqual([chapter.title for chapter in chapters], ["Chapter 1", "End"])

    def test_title_length_boundary_and_retry(self):
        def response(title):
            return json.dumps({"chapters": [{"start_line": 1, "end_line": 4, "title": title}], "cuts": []})

        valid = response("章" * 16)
        invalid = response("章" * 17)
        self.assertEqual(parse_youtube_chapter_response(valid, SUBTITLES)[0][0].title, "章" * 16)
        with self.assertRaisesRegex(ValueError, "34 half-width units; maximum is 32"):
            parse_youtube_chapter_response(invalid, SUBTITLES)
        refiner = HostedTextRefiner("test", [], ApiUsageLedger())
        with patch.object(refiner, "_chat", side_effect=[invalid, valid]) as chat:
            chapters = refiner.suggest_chapters(SUBTITLES)
        self.assertEqual(chapters[0].title, "章" * 16)
        self.assertEqual(chat.call_count, 2)
        self.assertIn("34 half-width units; maximum is 32", chat.call_args.args[0])
        self.assertEqual(refiner.last_youtube_chapters_raw, valid)

    def test_half_width_full_width_and_mixed_title_budgets(self):
        for title in ["a" * 32, "ｱ" * 32, "Ａ" * 16, "章" * 8 + "a" * 16, "章" * 15 + " a"]:
            with self.subTest(accepted=title):
                raw = json.dumps({"chapters": [{"start_line": 1, "end_line": 4, "title": title}]})
                self.assertEqual(parse_youtube_chapter_response(raw, SUBTITLES)[0][0].title, title)
        for title in ["a" * 33, "ｱ" * 33, "Ａ" * 17, "章" * 8 + "a" * 17, "章" * 15 + " ab"]:
            with self.subTest(rejected=title):
                raw = json.dumps({"chapters": [{"start_line": 1, "end_line": 4, "title": title}]})
                with self.assertRaisesRegex(ValueError, "half-width units; maximum is 32"):
                    parse_youtube_chapter_response(raw, SUBTITLES)

    def test_repeated_overlong_titles_never_create_markers(self):
        raw = json.dumps({"chapters": [{"start_line": 1, "end_line": 4, "title": "章" * 80}]})
        refiner = HostedTextRefiner("test", [], ApiUsageLedger())
        refiner.last_youtube_chapter_cuts = [{"after_line": 2}]
        with patch.object(refiner, "_chat", return_value=raw) as chat:
            self.assertEqual(refiner.suggest_chapters(SUBTITLES), [])
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(refiner.last_youtube_chapter_cuts, [])

    def test_gaps_are_filled_and_last_chapter_extends_to_end(self):
        raw = json.dumps(
            {
                "chapters": [
                    {"start_line": 2, "end_line": 2, "title": "Middle"},
                    {"start_line": 4, "end_line": 4, "title": "End"},
                ]
            }
        )

        chapters, _ = parse_youtube_chapter_response(raw, SUBTITLES)

        self.assertEqual((chapters[0].start_subtitle_index, chapters[0].end_subtitle_index), (1, 2))
        self.assertEqual((chapters[1].start_subtitle_index, chapters[1].end_subtitle_index), (3, 4))


if __name__ == "__main__":
    unittest.main()
