import unittest

from subtitler.models import AlignedToken, Subtitle
from subtitler.subtitle_planner import _left_merge_adjacent_subtitles


def _sub(text: str, start: float, end: float, chain: int, part: int) -> Subtitle:
    return Subtitle(
        start_time=start,
        end_time=end,
        text=text,
        tokens=[AlignedToken(text=text, start=start, end=end)],
        chain_index=chain,
        chain_part_index=part,
        split_source="llm",
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
