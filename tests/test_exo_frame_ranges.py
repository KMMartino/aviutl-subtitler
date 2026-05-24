import re
import unittest

from subtitler.exo import generate_exo_file
from subtitler.models import ExoSettings, Subtitle


class ExoFrameRangeTests(unittest.TestCase):
    def test_overlap_trimming_keeps_subtitle_objects_visible(self):
        exo = generate_exo_file(
            [
                Subtitle(1.0, 1.01, "first"),
                Subtitle(1.0, 1.5, "second"),
                Subtitle(1.6, 2.0, "third"),
            ],
            ExoSettings(rate=60),
            total_duration=3.0,
            insert_initial_empty=False,
        )

        ranges = [
            (int(start), int(end))
            for start, end in re.findall(r"start=(\d+)\nend=(\d+)", exo)
        ]
        self.assertEqual(len(ranges), 3)
        self.assertTrue(all(end > start for start, end in ranges))


if __name__ == "__main__":
    unittest.main()
