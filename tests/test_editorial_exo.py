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
    def test_wide_single_recording_becomes_cropped_gameplay_and_facecam_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "wide-recording.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [
                    EditorialSourceInput(
                        media,
                        10_000,
                        frame_rate=60,
                        width=3840,
                        height=1440,
                    )
                ],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000),
            )
            artifact["sources"][0]["stages"]["source_probe"]["output"] = {
                "frame_rate": 60,
                "visual_width": 3840,
                "visual_height": 1440,
                "wide_layout": {
                    "source_width": 3840,
                    "source_height": 1440,
                    "gameplay_width": 2560,
                    "facecam_left": 2560,
                    "facecam_bottom": 720,
                },
            }

            output = root / "wide.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        self.assertIn("width=2560\nheight=1440", exo)
        for layer in range(1, 5):
            self.assertIn(f"layer={layer}", exo)
        self.assertRegex(exo, r"(?s)layer=1.*?_name=クリッピング.*?右=1280")
        self.assertRegex(exo, r"(?s)layer=3.*?_name=クリッピング.*?下=720.*?左=2560")
        self.assertRegex(exo, r"(?s)layer=4.*?音量=0\.0")

    def test_1080p_source_scales_editorial_subtitle_size_and_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "game.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [
                    EditorialSourceInput(
                        media, 10_000, frame_rate=60, width=1920, height=1080
                    )
                ],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000),
            )
            timing = root / "timing.csv"
            timing.write_text("start,end\n1,2\n", encoding="utf-8")
            text = root / "text.txt"
            text.write_text("1. Line\n", encoding="utf-8")
            artifact["sources"][0]["stages"]["transcription"]["output"] = {
                "timing_path": str(timing),
                "text_path": str(text),
            }

            output = root / "1080.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        self.assertIn("width=1920\nheight=1080", exo)
        self.assertIn("サイズ=45", exo)
        self.assertIn("Y=356.40000000000003", exo)

    def test_primary_editorial_directions_are_html_only(self) -> None:
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

        self.assertNotIn("短縮".encode("utf-16le").hex(), exo)
        self.assertNotIn("この区間を最も強い場面に絞ります。".encode("utf-16le").hex(), exo)

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
                "start_ms": 3_000, "end_ms": 4_000, "text": "Emphasized only",
                "emphasis_energy": 1.0, "timing_verified": True,
            }]
            output = root / "partial.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")
        self.assertIn(encode_text_for_exo("Emphasized only"), exo)
        self.assertNotIn(encode_text_for_exo("Full transcript line"), exo)
        self.assertIn("Y=708.0", exo)
        self.assertEqual(exo.count("color=ff0000"), 2)

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
            self.assertEqual(exo.count(f"file={second_video.resolve()}"), 2)
            self.assertIn("start=601\nend=1320\nlayer=1", exo)
            self.assertRegex(
                exo,
                rf"(?ms)start=601\nend=1320\nlayer=3\n.*?file={re.escape(str(second_audio.resolve()))}",
            )
            self.assertRegex(
                exo,
                rf"(?ms)start=601\nend=1320\nlayer=4\n.*?file={re.escape(str(second_audio.resolve()))}",
            )
            self.assertIn("start=661\nend=721\nlayer=5", exo)
            self.assertNotIn("_name=カスタムオブジェクト", exo)
            self.assertNotIn(encode_text_for_exo("part-two-game-001"), exo)

    def test_only_local_supporting_edits_are_written_as_editorial_markers(self) -> None:
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
            artifact["editorial_map"]["creative_suggestions"] = [
                {"source_id": source_id, "start_ms": 5_000, "end_ms": 6_000, "type": "punch_in", "suggestion": "Punch in on the reaction"}
            ]

            output = root / "project.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

            self.assertIn("start=301\nend=361\nlayer=4", exo)
            self.assertIn("Punch in on the reaction".encode("utf-16le").hex(), exo)
            self.assertNotIn("Dead time".encode("utf-16le").hex(), exo)
            self.assertNotIn("Explain the skipped attempts".encode("utf-16le").hex(), exo)
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
            artifact["editorial_map"]["final_actions"] = [{
                "action_id": "action-001", "action_type": "preserve", "source_id": source_id,
                "start_ms": 0, "end_ms": 10_000, "instruction": "Keep it",
            }]
            artifact["editorial_map"]["supporting_edits"] = [
                {
                    "edit_id": f"edit-{index}", "parent_action_id": "action-001",
                    "action_type": action_type, "source_id": source_id,
                    "start_ms": 1_000, "end_ms": 2_000, "instruction": action_type,
                }
                for index, action_type in enumerate(
                    ("punch_in", "freeze_frame", "replay", "speed_change", "emphasize_text", "visual_gag"),
                    1,
                )
            ]

            output = root / "staggered.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        positions = [_object_y_for_layer(exo, layer) for layer in range(4, 10)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), 6)
        self.assertLess(positions[0], 0)
        self.assertGreater(positions[-1], 0)

    def test_non_overlapping_editorial_markers_share_a_layer_across_categories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "gameplay.mkv"
            source_path.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(source_path, 10_000, frame_rate=60)],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"]["final_actions"] = [{
                "action_id": "action-001", "action_type": "preserve", "source_id": source_id,
                "start_ms": 0, "end_ms": 10_000, "instruction": "Keep it",
            }]
            artifact["editorial_map"]["supporting_edits"] = [
                {
                    "edit_id": f"edit-{index}", "parent_action_id": "action-001",
                    "action_type": action_type, "source_id": source_id,
                    "start_ms": index * 2_000, "end_ms": index * 2_000 + 1_000,
                    "instruction": action_type,
                }
                for index, action_type in enumerate(("punch_in", "freeze_frame", "visual_gag"), 1)
            ]

            output = root / "packed.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        for action_type in ("punch_in", "freeze_frame", "visual_gag"):
            self.assertEqual(_object_layer_for_text(exo, action_type), 4)

    def test_final_actions_pre_split_linked_media_and_add_grouped_direction_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "gameplay.mkv"
            source_path.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(source_path, 10_000, frame_rate=60)],
                EditorialProjectOptions("Run", "Tell the story", 5_000, 9_000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"]["final_actions"] = [{
                "action_id": "action-001",
                "action_type": "preserve",
                "source_id": source_id,
                "start_ms": 0,
                "end_ms": 2_000,
                "instruction": "Keep the opening.",
            }, {
                "action_id": "action-002",
                "action_type": "trim",
                "source_id": source_id,
                "start_ms": 2_000,
                "end_ms": 4_000,
                "instruction": "Remove the repeated route.",
            }, {
                "action_id": "action-003",
                "action_type": "preserve",
                "source_id": source_id,
                "start_ms": 4_000,
                "end_ms": 10_000,
                "instruction": "Keep the payoff.",
            }]

            output = root / "chunked.exo"
            write_editorial_exo(output, artifact)
            exo = output.read_text(encoding="shift_jis")

        for start, end, source_start, group in (
            (1, 120, 1, 1),
            (121, 240, 121, 2),
            (241, 600, 241, 3),
        ):
            self.assertRegex(
                exo,
                rf"(?ms)start={start}\nend={end}\nlayer=1\ngroup={group}\n.*?再生位置={source_start}",
            )
            self.assertRegex(
                exo,
                rf"(?ms)start={start}\nend={end}\nlayer=2\ngroup={group}\n.*?動画ファイルと連携=1",
            )
        self.assertEqual(exo.count(f"file={source_path.resolve()}"), 6)
        for number, start, end, group in (
            ("1", 1, 120, 1),
            ("2", 121, 240, 2),
            ("3", 241, 600, 3),
        ):
            self.assertRegex(
                exo,
                rf"(?ms)start={start}\nend={end}\nlayer=4\ngroup={group}\n.*?text={encode_text_for_exo(number)}",
            )
        self.assertIn("align=0", exo)
        self.assertIn("X=-1268.0", exo)
        self.assertIn("Y=-722.0", exo)


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


def _object_layer_for_text(exo: str, text: str) -> int:
    encoded = text.encode("utf-16le").hex()
    for block in re.split(r"(?m)(?=^\[\d+\]\nstart=)", exo):
        if encoded not in block:
            continue
        match = re.search(r"(?m)^layer=(\d+)$", block)
        if match is not None:
            return int(match.group(1))
    raise AssertionError(f"No marker object found for {text}")


if __name__ == "__main__":
    unittest.main()
