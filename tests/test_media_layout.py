import unittest

from subtitler.media_layout import (
    WideRecordingLayout,
    _detect_facecam_bottom,
    cover_placement,
    top_right_overlay_placement,
    wide_recording_placements,
)


class MediaLayoutTests(unittest.TestCase):
    def test_detects_nonblack_facecam_above_black_right_panel(self) -> None:
        width, height, right_start = 120, 90, 80
        frame = bytearray(width * height)
        for y in range(height):
            for x in range(width):
                if x < right_start or y < 45:
                    frame[y * width + x] = 180

        self.assertEqual(
            _detect_facecam_bottom([bytes(frame)] * 3, width, height, right_start),
            45,
        )

    def test_rejects_wide_frame_without_black_lower_right_region(self) -> None:
        width, height, right_start = 120, 90, 80
        frame = bytes([180] * (width * height))

        self.assertIsNone(
            _detect_facecam_bottom([frame], width, height, right_start)
        )

    def test_wide_layout_crops_gameplay_and_places_facecam_at_top_right(self) -> None:
        layout = WideRecordingLayout(3840, 1440, 2560, 2560, 720)

        primary, facecam = wide_recording_placements(layout)

        self.assertEqual(primary.crop, (0, 0, 0, 1280))
        self.assertEqual(facecam.crop, (0, 720, 2560, 0))
        self.assertAlmostEqual(facecam.scale_percent, 64.0)
        self.assertAlmostEqual(facecam.x, 870.4)
        self.assertAlmostEqual(facecam.y, -489.6)

    def test_cover_and_overlay_placements_adapt_to_source_resolution(self) -> None:
        self.assertAlmostEqual(
            cover_placement(3840, 2160, 1920, 1080).scale_percent, 200.0
        )
        overlay = top_right_overlay_placement(1920, 1080, 1280, 720)
        self.assertAlmostEqual(overlay.scale_percent, 48.0)
        self.assertAlmostEqual(overlay.x, 652.8)
        self.assertAlmostEqual(overlay.y, -367.2)


if __name__ == "__main__":
    unittest.main()
