import tempfile
import unittest
import shutil
from pathlib import Path

import numpy as np

from subtitler.frame_motion import sampled_regional_motion


def write_image(path, pixels):
    path.write_bytes(b"P5\n320 180\n255\n" + pixels.tobytes())


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required for sampled-image decoding")
class FrameMotionTests(unittest.TestCase):
    def test_static_background_is_detected_despite_moving_corner_and_low_level_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(4):
                pixels = np.full((180, 320), 80 + index % 2, dtype=np.uint8)
                pixels[:45, :40] = 20 if index % 2 else 230
                path = Path(directory) / f"{index}.pgm"
                write_image(path, pixels)
                paths.append(path)
            originals = [path.read_bytes() for path in paths]
            spans = sampled_regional_motion(paths, [1000, 13000, 25000, 37000])
            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual((span["start_ms"], span["end_ms"]), (1000, 37000))
            self.assertEqual(span["moving_cells"], [0])
            self.assertNotIn(0, span["unchanged_cells"])
            self.assertEqual(span["observation"], "dominant_region_unchanged_with_local_motion")
            self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_broad_changes_end_static_runs_and_short_or_invalid_samples_do_not_claim_stillness(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, value in enumerate([40, 40, 40, 200, 40, 200]):
                path = Path(directory) / f"{index}.pgm"
                write_image(path, np.full((180, 320), value, dtype=np.uint8))
                paths.append(path)
            spans = sampled_regional_motion(paths, [0, 12000, 24000, 36000, 48000, 60000])
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0]["end_ms"], 24000)
            self.assertEqual(spans[0]["unchanged_cells"], list(range(32)))
            self.assertEqual(spans[0]["observation"], "sampled_frame_unchanged")
            self.assertEqual(sampled_regional_motion(paths[2:], [0, 12000, 24000, 36000]), [])
            self.assertEqual(sampled_regional_motion(paths[:2], [0, 12000]), [])
            for stamps in ([0], [0, 0], [12000, 0]):
                with self.subTest(stamps=stamps), self.assertRaises(ValueError):
                    sampled_regional_motion(paths[:2], stamps)
