import re
import unittest
from pathlib import Path

from subtitler.exo import _chapter_marker_frame_ranges, encode_text_for_exo, generate_exo_file
from subtitler.models import BrollPlacement, ExoMarker, ExoMediaPlan, ExoMediaSegment, ExoSettings, Subtitle


class ExoMarkerTests(unittest.TestCase):
    def test_multiline_text_uses_crlf_for_aviutl_editor_compatibility(self) -> None:
        encoded = encode_text_for_exo("First\nSecond\rThird\r\nFourth")
        decoded = bytes.fromhex(encoded).decode("utf-16-le").split("\0", 1)[0]

        self.assertEqual(decoded, "First\r\nSecond\r\nThird\r\nFourth")
        self.assertNotRegex(decoded, r"(?<!\r)\n")

    def test_broll_sits_below_source_and_above_subtitles_with_linked_audio(self) -> None:
        media_plan = ExoMediaPlan(
            source_path=Path("C:/media/source.mp4"),
            segments=[ExoMediaSegment(1, 180, 1, 1)],
        )
        placement = BrollPlacement(
            id="broll-1",
            asset_id="asset",
            asset_path=Path("C:/media/gameplay.mp4"),
            media_kind="video",
            output_start_frame=61,
            output_end_frame=120,
            source_start_frame=301,
            confidence=.9,
            reason="relevant",
            has_audio=True,
        )
        content = generate_exo_file(
            [Subtitle(1.0, 2.0, "subtitle")],
            ExoSettings(),
            3.0,
            chapter_markers=[ExoMarker(0.0, 3.0, "Chapter")],
            mistranscription_markers=[ExoMarker(0.2, 0.4, "check")],
            media_plan=media_plan,
            broll_placements=[placement],
        )
        self.assertRegex(content, r"(?s)start=1\nend=180\nlayer=1\ngroup=1.*?_name=動画ファイル")
        self.assertRegex(content, r"(?s)start=1\nend=180\nlayer=2\ngroup=1.*?_name=音声ファイル")
        self.assertRegex(content, r"(?s)start=61\nend=120\nlayer=3\ngroup=10002.*?_name=動画ファイル")
        self.assertRegex(content, r"(?s)start=61\nend=120\nlayer=4\ngroup=10002.*?_name=音声ファイル")
        self.assertIn("音量=0.0", content)
        self.assertRegex(content, r"(?s)start=1\nend=181\nlayer=5.*?_name=図形")
        self.assertRegex(content, r"(?s)start=61\nend=121\nlayer=6.*?_name=テキスト")
        self.assertRegex(content, r"(?s)start=13\nend=24\nlayer=7.*?_name=テキスト")
        self.assertRegex(content, r"(?s)start=1\nend=181\nlayer=8.*?_name=カスタムオブジェクト")
        self.assertRegex(content, r"(?s)start=1\nend=181\nlayer=9.*?_name=カスタムオブジェクト")
        self.assertRegex(content, r"(?s)start=1\nend=181\nlayer=10.*?_name=テキスト")

    def test_broll_keeps_primary_visual_intact(self) -> None:
        media_plan = ExoMediaPlan(
            source_path=Path("C:/media/source.mp4"),
            segments=[ExoMediaSegment(1, 180, 1, 1)],
        )
        placement = BrollPlacement(
            id="broll-1",
            asset_id="asset",
            asset_path=Path("C:/media/still.png"),
            media_kind="image",
            output_start_frame=61,
            output_end_frame=120,
            source_start_frame=1,
            confidence=.9,
            reason="relevant",
        )
        content = generate_exo_file(
            [],
            ExoSettings(),
            3.0,
            media_plan=media_plan,
            broll_placements=[placement],
        )
        self.assertRegex(content, r"(?s)start=1\nend=180\nlayer=1\ngroup=1.*?_name=動画ファイル")
        self.assertIn("start=1\nend=180\nlayer=2", content)
        self.assertRegex(content, r"(?s)start=61\nend=120\nlayer=3\ngroup=10002.*?_name=画像ファイル")
        self.assertNotIn("start=1\nend=60\nlayer=1", content)
        self.assertNotIn("start=121\nend=180\nlayer=1", content)

    def test_chapter_and_qa_markers_use_separate_layers(self) -> None:
        content = generate_exo_file(
            [Subtitle(0.0, 1.0, "line")],
            ExoSettings(),
            total_duration=2.0,
            chapter_markers=[ExoMarker(0.0, 1.0, "Intro")],
            mistranscription_markers=[ExoMarker(0.2, 0.4, "1: high - reason")],
        )
        for layer in range(1, 7):
            self.assertIn(f"layer={layer}", content)
        self.assertIn(encode_text_for_exo("Intro"), content)
        self.assertIn(encode_text_for_exo("1: high - reason"), content)
        self.assertEqual(content.count("_name=カスタムオブジェクト"), 2)
        self.assertEqual(content.count("_name=図形"), 1)
        self.assertIn("サイズ=2615", content)
        self.assertIn("Y=1937.0", content)
        self.assertIn("透明度=50.0", content)
        self.assertIn("_name=斜めクリッピング", content)
        self.assertIn("_name=アニメーション効果", content)
        self.assertIn("track0=0.20", content)
        self.assertIn("track0=-0.20", content)
        self.assertIn("サイズ=33", content)
        self.assertIn("color=ff0000", content)
        self.assertIn("Y=642.0", content)

    def test_chapter_style_matches_reference_geometry_and_transition_timing(self) -> None:
        content = generate_exo_file(
            [],
            ExoSettings(),
            total_duration=20.0,
            chapter_markers=[
                ExoMarker(0.0, 10.0, "サンプルテキスト1"),
                ExoMarker(10.0, 20.0, "より長いサンプルテキスト1"),
            ],
        )

        self.assertEqual(content.count("_name=カスタムオブジェクト"), 6)
        self.assertEqual(content.count("_name=斜めクリッピング"), 4)
        self.assertEqual(
            content.count("track0=560.00,780.00,15@イージング（通常）@イージング,23"),
            2,
        )
        self.assertIn("中心X=-330,330,1", content)
        self.assertIn("中心X=-440,440,1", content)
        self.assertIn("中心X=280,-280,15@イージング（通常）@イージング,23", content)
        self.assertIn("中心X=-390,390,15@イージング（通常）@イージング,23", content)

        background_heads = re.findall(
            r"start=601\nend=648\nlayer=([45]).*?"
            r"track0=560\.00,780\.00,15@イージング（通常）@イージング,23",
            content,
            flags=re.DOTALL,
        )
        self.assertEqual(background_heads, ["4", "5"])
        self.assertRegex(content, r"start=601\nend=744\nlayer=6\noverlay=1")
        self.assertNotIn("group=20000", content)
        self.assertIn("color=000000\nno_color=0\ncolor2=ffffff", content)

    def test_chapters_cover_the_complete_timeline_without_subtitle_gaps(self) -> None:
        ranges = _chapter_marker_frame_ranges(
            [
                ExoMarker(5.45, 74.0, "First"),
                ExoMarker(75.9, 188.0, "Second"),
                ExoMarker(190.2, 200.0, "Third"),
            ],
            fps=60,
            total_frames=12_001,
        )
        self.assertEqual(
            ranges,
            [
                (1, 4554, "First"),
                (4555, 11_412, "Second"),
                (11_413, 12_001, "Third"),
            ],
        )

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
        for layer in range(1, 9):
            self.assertIn(f"layer={layer}", content)
        self.assertEqual(content.count("_name=アニメーション効果"), 2)
        reference = (Path(__file__).parent / "fixtures" / "cut-video-example-minimal.exo").read_text(encoding="utf-8")
        for field in ("_name=動画ファイル", "_name=音声ファイル", "動画ファイルと連携=1", "再生速度=100.0"):
            self.assertIn(field, reference)
            self.assertIn(field, content)


if __name__ == "__main__":
    unittest.main()
