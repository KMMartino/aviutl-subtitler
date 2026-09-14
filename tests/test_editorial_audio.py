import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from subtitler.editorial_audio import prepare_editorial_audio
from subtitler.editorial_enrichment import analyze_acoustic_emphasis
from subtitler.errors import SubtitlerError
from subtitler.exo import generate_exo_file
from subtitler.models import ExoCompositeMediaClip, ExoMediaSegment, ExoSettings


class EditorialAudioTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for media verification")
    def test_separate_selected_tracks_remain_distinct_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            media = root / "combined.mkv"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "color=size=32x32:rate=10:duration=2", "-f", "lavfi", "-i",
                "sine=frequency=220:duration=2", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=2", "-f", "lavfi", "-i",
                "sine=frequency=880:duration=2", "-map", "0:v", "-map", "1:a",
                "-map", "2:a", "-map", "3:a", "-c:v", "ffv1", "-c:a", "pcm_s16le", str(media),
            ], check=True, capture_output=True)
            source = {"visual_path": str(media), "media_mode": "single",
                      "audio_track": 1, "game_audio_track": 2}
            artifact = {"sources": [source]}
            prepare_editorial_audio(artifact, root / "assets")
            exports = source["export_audio"]
            for role, frequency in (("primary_path", 880), ("overlay_path", 440)):
                with self.subTest(role=role), wave.open(exports[role], "rb") as wav:
                    signal = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
                    peak = np.argmax(np.abs(np.fft.rfft(signal))) * wav.getframerate() / len(signal)
                    self.assertAlmostEqual(peak, frequency, delta=1)
            with patch("subtitler.editorial_audio.subprocess.run") as run:
                prepare_editorial_audio(artifact, root / "assets")
                run.assert_not_called()
                source.update(media_mode="paired", audio_path="facecam.mkv", speech_source="gameplay")
                prepare_editorial_audio(artifact, root / "assets")
                self.assertEqual(source["export_audio"], exports)
                run.assert_not_called()
            source["game_audio_track"] = 9
            with self.assertRaises(SubtitlerError):
                prepare_editorial_audio(artifact, root / "assets")
            self.assertEqual(len(list((root / "assets").glob("*.wav"))), 2)

    def test_external_audio_follows_source_time_after_a_cut(self) -> None:
        content = generate_exo_file([], ExoSettings(rate=60), 1.0, composite_media_clips=[
            ExoCompositeMediaClip(
                video_path=Path("combined.mkv"), audio_path=Path("game.wav"),
                overlay_audio_path=Path("voice.wav"),
                segment=ExoMediaSegment(1, 60, 121, 1),
            ),
        ])
        self.assertEqual(content.count("再生位置=2.00"), 2)
        self.assertEqual(content.count("動画ファイルと連携=0"), 2)
        self.assertIn("layer=2", content)
        self.assertIn("layer=4", content)
        self.assertEqual(content.count("_name=動画ファイル"), 1)

    def test_default_audio_keeps_original_and_acoustics_use_zero_based_track(self) -> None:
        source = {"visual_path": "combined.mkv", "media_mode": "single"}
        with patch("subtitler.editorial_audio.subprocess.run") as run:
            prepare_editorial_audio({"sources": [source]}, Path("unused"))
            run.assert_not_called()
        self.assertEqual(source["export_audio"]["primary_path"], str(Path("combined.mkv").resolve()))
        self.assertIsNone(source["export_audio"]["overlay_path"])
        with patch("subtitler.editorial_enrichment.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "fixture unavailable"
            analyze_acoustic_emphasis(Path("combined.mkv"), duration_ms=2000, audio_track=1)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-map") + 1], "0:a:1")
