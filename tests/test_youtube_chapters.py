import json
import unittest

from subtitler.external_refiners import parse_youtube_chapter_response


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
                    {"start_line": 1, "end_line": 2, "title": "Intro and History"},
                    {"start_line": 3, "end_line": 4, "title": "Current and Future"},
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
        self.assertEqual(chapters[0].title, "Intro and History")
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

    def test_title_fallback_for_empty_or_long_titles(self):
        raw = json.dumps(
            {
                "chapters": [
                    {"start_line": 1, "end_line": 2, "title": ""},
                    {"start_line": 3, "end_line": 4, "title": "x" * 80},
                ]
            }
        )

        chapters, _ = parse_youtube_chapter_response(raw, SUBTITLES)

        self.assertEqual([chapter.title for chapter in chapters], ["Chapter 1", "Chapter 2"])

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
