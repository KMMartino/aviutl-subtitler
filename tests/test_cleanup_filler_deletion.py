import unittest

from subtitler.models import Subtitle
from subtitler.subtitle_planner import _apply_cleanup_refinements


class CleanupFillerDeletionTests(unittest.TestCase):
    def test_adjacent_filler_deletions_extend_previous_subtitle(self) -> None:
        subtitles = [
            Subtitle(0.0, 1.0, "前"),
            Subtitle(1.0, 2.0, "えー"),
            Subtitle(2.0, 3.0, "あの、"),
            Subtitle(3.0, 4.0, "後"),
        ]

        _apply_cleanup_refinements(subtitles, {1: "", 2: ""}, [])

        self.assertEqual([(sub.start_time, sub.end_time, sub.text) for sub in subtitles], [(0.0, 3.0, "前"), (3.0, 4.0, "後")])

    def test_leading_filler_deletion_extends_next_subtitle_backwards(self) -> None:
        subtitles = [Subtitle(0.0, 1.0, "うーん"), Subtitle(1.0, 2.0, "内容")]

        _apply_cleanup_refinements(subtitles, {0: ""}, [])

        self.assertEqual([(sub.start_time, sub.end_time, sub.text) for sub in subtitles], [(0.0, 2.0, "内容")])

    def test_only_subtitle_can_be_deleted_without_coverage(self) -> None:
        subtitles = [Subtitle(0.0, 1.0, "まあ")]
        _apply_cleanup_refinements(subtitles, {0: ""}, [])
        self.assertEqual(subtitles, [])

    def test_empty_non_filler_is_not_deleted_defensively(self) -> None:
        subtitles = [Subtitle(0.0, 1.0, "内容")]
        _apply_cleanup_refinements(subtitles, {0: ""}, [])
        self.assertEqual(subtitles[0].text, "内容")


if __name__ == "__main__":
    unittest.main()
