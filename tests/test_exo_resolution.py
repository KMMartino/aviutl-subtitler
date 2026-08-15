import re
import unittest
from pathlib import Path

from subtitler.exo import generate_exo_file
from subtitler.models import (
    ExoCompositeMediaClip,
    ExoMarker,
    ExoMediaSegment,
    ExoSettings,
    Subtitle,
)


class ExoResolutionTests(unittest.TestCase):
    def test_1080p_scales_subtitle_chapter_and_corner_marker_layout(self) -> None:
        settings = ExoSettings(
            width=1920,
            height=1080,
            font_size=45,
            y_position=537.75,
        )
        content = generate_exo_file(
            [Subtitle(0, 1, "subtitle")],
            settings,
            2,
            chapter_markers=[ExoMarker(0, 2, "Chapter")],
            segment_number_markers=[ExoMarker(0, 1, "1")],
        )

        self.assertIn("width=1920\nheight=1080", content)
        self.assertIn("サイズ=45", content)
        self.assertIn("Y=537.75", content)
        self.assertIn("X=-951.0", content)
        self.assertIn("Y=-541.5", content)
        chapter_x = re.search(r"name=角丸四角形@hksy.*?\nX=(-?\d+\.\d+)", content, re.S)
        self.assertIsNotNone(chapter_x)
        self.assertGreater(float(chapter_x.group(1)), -960.0)

    def test_video_objects_emit_adaptive_crop_scale_and_position(self) -> None:
        source = Path("C:/media/wide.mp4")
        clip = ExoCompositeMediaClip(
            source,
            source,
            ExoMediaSegment(1, 60, 1, 1),
            overlay_video_path=source,
            overlay_audio_path=source,
            video_crop=(0, 0, 0, 1280),
            overlay_crop=(0, 720, 2560, 0),
            overlay_scale_percent=64.0,
            overlay_x=870.4,
            overlay_y=-489.6,
            overlay_audio_volume=0.0,
        )

        content = generate_exo_file([], ExoSettings(), 1, composite_media_clips=[clip])

        self.assertRegex(content, r"(?s)layer=1.*?_name=クリッピング.*?右=1280")
        self.assertRegex(content, r"(?s)layer=3.*?_name=クリッピング.*?下=720.*?左=2560")
        self.assertEqual(content.count("中心の位置を変更=1"), 2)
        self.assertRegex(content, r"(?s)layer=3.*?X=870\.4\nY=-489\.6.*?拡大率=64\.00")
        self.assertRegex(content, r"(?s)layer=4.*?音量=0\.0")


if __name__ == "__main__":
    unittest.main()
