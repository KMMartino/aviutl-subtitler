import unittest

from subtitler.models import AlignedToken, Subtitle
from subtitler.subtitle_planner import _cleanup_windows, _left_merge_adjacent_subtitles, _strip_standard_sentence_periods


def _sub(text: str, start: float, end: float, chain: int, part: int, source: str = "llm") -> Subtitle:
    return Subtitle(
        start_time=start,
        end_time=end,
        text=text,
        tokens=[AlignedToken(text=text, start=start, end=end)],
        chain_index=chain,
        chain_part_index=part,
        split_source=source,
    )


def _cleanup_sub(text: str, chain: int, cleanup_group: int | None) -> Subtitle:
    return Subtitle(
        start_time=0.0,
        end_time=1.0,
        text=text,
        chain_index=chain,
        cleanup_group_index=cleanup_group,
    )


class SubtitleLeftMergeTests(unittest.TestCase):
    def test_left_merge_adjacent_subtitles_under_max_chars(self) -> None:
        subtitles = [
            _sub("短い", 0.0, 0.5, 0, 0),
            _sub("字幕です", 0.5, 1.0, 0, 1),
            _sub("これは長めの字幕なので残します", 2.0, 3.0, 1, 0),
        ]

        merged = _left_merge_adjacent_subtitles(subtitles, max_chars=10)

        self.assertEqual(merged, 1)
        self.assertEqual([sub.text for sub in subtitles], ["短い字幕です", "これは長めの字幕なので残します"])
        self.assertEqual(subtitles[0].start_time, 0.0)
        self.assertEqual(subtitles[0].end_time, 1.0)
        self.assertEqual([token.text for token in subtitles[0].tokens], ["短い", "字幕です"])
        self.assertEqual(subtitles[0].chain_part_index, 0)
        self.assertEqual(subtitles[0].timing_adjustment, "left_merge")

    def test_left_merge_does_not_cross_chain_boundary(self) -> None:
        subtitles = [
            _sub("短い", 0.0, 0.5, 0, 0),
            _sub("字幕", 0.5, 1.0, 1, 0),
        ]

        merged = _left_merge_adjacent_subtitles(subtitles, max_chars=10)

        self.assertEqual(merged, 0)
        self.assertEqual([sub.text for sub in subtitles], ["短い", "字幕"])
        self.assertEqual([sub.chain_index for sub in subtitles], [0, 1])

    def test_left_merge_does_not_follow_sentence_terminal_split(self) -> None:
        subtitles = [
            _sub("文末。", 0.0, 0.5, 0, 0, "structural_sentence+sentence_terminal"),
            _sub("次", 0.5, 1.0, 0, 1),
        ]

        merged = _left_merge_adjacent_subtitles(subtitles, max_chars=10)

        self.assertEqual(merged, 0)
        self.assertEqual([sub.text for sub in subtitles], ["文末。", "次"])

    def test_strip_standard_sentence_periods_is_programmatic(self) -> None:
        subtitles = [
            _sub("文末。", 0.0, 0.5, 0, 0, "structural_sentence+sentence_terminal"),
            _sub("感嘆！", 0.5, 1.0, 0, 1, "structural_sentence+sentence_terminal"),
        ]

        stripped = _strip_standard_sentence_periods(subtitles)

        self.assertEqual(stripped, 1)
        self.assertEqual([sub.text for sub in subtitles], ["文末", "感嘆！"])
        self.assertIn("strip_sentence_period", subtitles[0].timing_adjustment)

    def test_cleanup_windows_do_not_cross_cleanup_group_boundaries(self) -> None:
        subtitles = [
            _cleanup_sub("a", 0, 10),
            _cleanup_sub("b", 0, 10),
            _cleanup_sub("c", 0, 11),
            _cleanup_sub("d", 0, 11),
            _cleanup_sub("e", 1, 20),
        ]

        windows = _cleanup_windows(subtitles, window_size=10)

        self.assertEqual(windows, [(0, 2), (2, 4), (4, 5)])

    def test_cleanup_windows_fall_back_to_chain_boundaries(self) -> None:
        subtitles = [
            _cleanup_sub("a", 0, None),
            _cleanup_sub("b", 0, None),
            _cleanup_sub("c", 1, None),
        ]

        windows = _cleanup_windows(subtitles, window_size=10)

        self.assertEqual(windows, [(0, 2), (2, 3)])
