import re
import tempfile
import unittest
from pathlib import Path

from subtitler.editorial_exo import write_editorial_exo
from subtitler.exo import encode_text_for_exo
from subtitler.editorial_project import (
    EditorialProjectOptions,
    EditorialSourceInput,
    create_editorial_project,
)


class EditorialExoTests(unittest.TestCase):
    def test_japanese_project_localizes_editorial_marker_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "game.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions(
                    "Run",
                    "Tell the story",
                    5_000,
                    9_000,
                    output_locale="ja",
                ),
            )
            artifact["editorial_map"]["recommendations"] = [
                {
                    "source_id": artifact["sources"][0]["source_id"],
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "disposition": "condense",
                    "presentation_mode": "live_excerpt",
                }
            ]

            output = root / "project.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        self.assertIn("短縮".encode("utf-16le").hex(), exo)
        self.assertIn("この区間を最も強い場面に絞ります。".encode("utf-16le").hex(), exo)

    def test_partial_mode_writes_only_verified_emphasized_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "game.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000, subtitle_mode="emphasis"),
            )
            timing = root / "timing.csv"
            timing.write_text("start,end\n1,2\n", encoding="utf-8")
            text = root / "text.txt"
            text.write_text("1. Full transcript line\n", encoding="utf-8")
            artifact["sources"][0]["stages"]["transcription"]["output"] = {
                "timing_path": str(timing), "text_path": str(text)
            }
            artifact["editorial_map"]["emphasized_phrases"] = [{
                "source_id": artifact["sources"][0]["source_id"],
                "start_ms": 3_000, "end_ms": 4_000, "text": "Emphasized only", "timing_verified": True,
            }]
            output = root / "partial.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")
        self.assertIn(encode_text_for_exo("Emphasized only"), exo)
        self.assertNotIn(encode_text_for_exo("Full transcript line"), exo)

    def test_links_chronological_media_and_offsets_transcript_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "part-one.mp4"
            second_video = root / "part-two-game.mp4"
            second_audio = root / "part-two-face.mp4"
            for path in (first, second_video, second_audio):
                path.write_bytes(path.name.encode())
            artifact = create_editorial_project(
                [
                    EditorialSourceInput(first, 10_000, frame_rate=60),
                    EditorialSourceInput(
                        second_video,
                        12_000,
                        audio_path=second_audio,
                        visual_path=second_video,
                        audio_duration_ms=12_000,
                        visual_duration_ms=12_000,
                        frame_rate=60,
                        media_mode="paired",
                        pairing_basis="filename",
                    ),
                ],
                EditorialProjectOptions("Run", "Tell the story", 10_000, 20_000),
            )
            timing = root / "timing.csv"
            timing.write_text("start,end\n1.0,2.0\n", encoding="utf-8")
            text = root / "text.txt"
            text.write_text("1. Spoken line\n", encoding="utf-8")
            artifact["sources"][1]["stages"]["transcription"]["output"] = {
                "timing_path": str(timing),
                "text_path": str(text),
            }
            source_id = artifact["sources"][1]["source_id"]
            artifact["editorial_map"]["recommendations"] = [
                {
                    "id": "direction-42",
                    "source_id": source_id,
                    "start_ms": 2_000,
                    "end_ms": 4_000,
                    "disposition": "condense",
                    "presentation_mode": "narration_montage",
                    "reason": "Summarize repeated attempts",
                }
            ]

            output = root / "project.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

            self.assertIn(f"file={first.resolve()}", exo)
            self.assertIn(f"file={second_video.resolve()}", exo)
            self.assertIn(f"file={second_audio.resolve()}", exo)
            self.assertEqual(exo.count(f"file={second_audio.resolve()}"), 2)
            self.assertIn("start=601\nend=1320\nlayer=1", exo)
            self.assertRegex(
                exo,
                rf"(?ms)start=601\nend=1320\nlayer=3\n.*?file={re.escape(str(second_audio.resolve()))}",
            )
            self.assertIn("start=661\nend=721\nlayer=4", exo)
            self.assertIn("start=721\nend=841\nlayer=7", exo)
            self.assertNotIn("_name=カスタムオブジェクト", exo)
            self.assertIn(
                encode_text_for_exo(
                    "part-two-game-001\nMONTAGE + VOICEOVER\nReplace most of this section with\nconcise narration and\nrepresentative footage. Summarize\nrepeated attempts"
                ),
                exo,
            )

    def test_editorial_actions_use_stable_separate_layers_without_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "gameplay.mkv"
            source_path.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(source_path, 10_000, frame_rate=60)],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"]["recommendations"] = [
                {"source_id": source_id, "start_ms": 0, "end_ms": 1_000, "disposition": "omit", "presentation_mode": "live_excerpt", "reason": "Dead time"},
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "disposition": "condense", "presentation_mode": "live_excerpt", "reason": "Repeated route"},
                {"source_id": source_id, "start_ms": 2_000, "end_ms": 3_000, "disposition": "keep", "presentation_mode": "live", "reason": "Payoff"},
                {"source_id": source_id, "start_ms": 3_000, "end_ms": 4_000, "disposition": "connect", "presentation_mode": "live_excerpt", "reason": "Related topic"},
            ]
            artifact["editorial_map"]["narration_briefs"] = [
                {"source_id": source_id, "start_ms": 4_000, "end_ms": 5_000, "purpose": "Explain the skipped attempts"}
            ]

            output = root / "project.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

            for layer in range(4, 9):
                self.assertIn(f"layer={layer}", exo)
            self.assertNotIn(encode_text_for_exo("Part 1: gameplay.mkv"), exo)
            self.assertNotIn("_name=カスタムオブジェクト", exo)
            self.assertNotIn(encode_text_for_exo(source_id), exo)

    def test_simultaneous_editorial_marker_layers_are_staggered_top_to_bottom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "gameplay.mkv"
            source_path.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(source_path, 10_000, frame_rate=60)],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"]["recommendations"] = [
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "disposition": "omit"},
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "disposition": "condense"},
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "disposition": "keep"},
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "disposition": "connect"},
            ]
            artifact["editorial_map"]["narration_briefs"] = [
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "purpose": "Bridge it"}
            ]
            artifact["editorial_map"]["creative_suggestions"] = [
                {"source_id": source_id, "start_ms": 1_000, "end_ms": 2_000, "type": "punch_in", "suggestion": "Punch in"}
            ]

            output = root / "staggered.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        positions = [_object_y_for_layer(exo, layer) for layer in range(4, 10)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), 6)
        self.assertLess(positions[0], 0)
        self.assertGreater(positions[-1], 0)


def _object_y_for_layer(exo: str, layer: int) -> float:
    match = re.search(
        rf"(?ms)^\[\d+\]\nstart=61\nend=121\nlayer={layer}\n.*?(?=^\[\d+\]\nstart=|\Z)",
        exo,
    )
    if match is None:
        raise AssertionError(f"No marker object found on layer {layer}")
    values = re.findall(r"(?m)^Y=(-?\d+(?:\.\d+)?)$", match.group(0))
    if not values:
        raise AssertionError(f"No Y position found on layer {layer}")
    return float(values[-1])


if __name__ == "__main__":
    unittest.main()
