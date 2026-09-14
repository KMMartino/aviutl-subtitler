import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.errors import SubtitlerError
from subtitler.media_export import ExoExportRequest, SourceLayout, export_exo, prepare_source_layout
from subtitler.media_layout import WideRecordingLayout
from subtitler.models import ExoMediaPlan, ExoMediaSegment, ExoSettings, Subtitle


class MediaExportTests(unittest.TestCase):
    def test_audio_only_layout_uses_configured_canvas_and_scales_subtitles(self) -> None:
        with patch("subtitler.source_inspection.probe_video_geometry", side_effect=SubtitlerError("audio only")):
            layout = prepare_source_layout(Path("audio.wav"), {
                "width": 1920, "height": 1080, "fps": 60, "font": "test", "font_size": 60, "y_position": 700,
            })
        self.assertEqual((layout.settings.width, layout.settings.height), (1920, 1080))
        self.assertEqual((layout.settings.font_size, layout.settings.y_position), (45, 525))
        self.assertIsNone(layout.wide_recording)

    def test_wide_source_export_retains_cut_offsets_and_mutes_duplicate_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wide.exo"
            plan = ExoMediaPlan(Path("recording.mkv"), [ExoMediaSegment(1, 60, 121, 1)])
            export_exo(ExoExportRequest(
                source_path=Path("recording.mkv"), output_path=output, duration_sec=1,
                layout=SourceLayout(ExoSettings(), WideRecordingLayout(3840, 1440, 2560, 2560, 720)),
                subtitles=[Subtitle(0, 1, "字幕")], media_plan=plan,
            ))
            content = output.read_text(encoding="cp932")
            self.assertIn("layer=1", content)
            self.assertIn("layer=2", content)
            self.assertIn("layer=3", content)
            self.assertIn("layer=4", content)
            self.assertIn("layer=5", content)
            self.assertIn("音量=0.0", content)
            self.assertIn("再生位置=121\n", content)
            self.assertEqual(plan.segments[0].source_start_frame, 121)
