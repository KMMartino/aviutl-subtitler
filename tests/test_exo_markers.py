import unittest
from pathlib import Path

from subtitler.exo import encode_text_for_exo, generate_exo_file
from subtitler.models import ExoMarker, ExoMediaPlan, ExoMediaSegment, ExoSettings, Subtitle


class ExoMarkerTests(unittest.TestCase):
    def test_chapter_and_qa_markers_use_separate_layers(self) -> None:
        content = generate_exo_file(
            [Subtitle(0.0, 1.0, "line")],
            ExoSettings(),
            total_duration=2.0,
            chapter_markers=[ExoMarker(0.0, 1.0, "Intro")],
            mistranscription_markers=[ExoMarker(0.2, 0.4, "1: high - reason")],
        )
        self.assertIn("layer=1", content)
        self.assertIn("layer=2", content)
        self.assertIn("layer=3", content)
        self.assertNotIn("layer=4", content)
        self.assertIn(encode_text_for_exo("Intro"), content)
        self.assertIn(encode_text_for_exo("1: high - reason"), content)
        self.assertIn("_name=アニメーション効果", content)
        self.assertIn("track0=0.20", content)
        self.assertIn("track0=-0.20", content)
        self.assertIn("サイズ=33", content)
        self.assertIn("color=ff0000", content)
        self.assertIn("Y=642.0", content)

    def test_composite_media_objects_shift_text_layers_and_link_audio(self) -> None:
        media = ExoMediaPlan(
            Path("C:/media/source.mkv"),
            [ExoMediaSegment(1, 60, 1, 1), ExoMediaSegment(61, 120, 181, 2)],
        )
        content = generate_exo_file(
            [Subtitle(0.0, 1.0, "line")],
            ExoSettings(rate=60),
            total_duration=2.0,
            chapter_markers=[ExoMarker(0.0, 1.0, "Intro")],
            mistranscription_markers=[ExoMarker(0.2, 0.4, "check")],
            media_plan=media,
        )
        self.assertEqual(content.count("_name=動画ファイル"), 2)
        self.assertEqual(content.count("_name=音声ファイル"), 2)
        self.assertEqual(content.count("動画ファイルと連携=1"), 2)
        self.assertIn("再生位置=181", content)
        for layer in range(1, 6):
            self.assertIn(f"layer={layer}", content)
        self.assertEqual(content.count("_name=アニメーション効果"), 2)
        reference = (Path(__file__).parent / "fixtures" / "cut-video-example-minimal.exo").read_text(encoding="utf-8")
        for field in ("_name=動画ファイル", "_name=音声ファイル", "動画ファイルと連携=1", "再生速度=100.0"):
            self.assertIn(field, reference)
            self.assertIn(field, content)


if __name__ == "__main__":
    unittest.main()
