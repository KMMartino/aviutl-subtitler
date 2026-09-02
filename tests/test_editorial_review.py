import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from subtitler.editorial_exo import write_editorial_exo
from subtitler.exo import encode_text_for_exo
from subtitler.editorial_project import (
    EditorialProjectOptions,
    EditorialSourceInput,
    create_editorial_project,
    write_editorial_checkpoint,
)
from subtitler.editorial_review import (
    _map_markers_to_sources,
    _is_narration_text,
    _narration_direction,
    _narration_markers,
    _text_markers,
    apply_reviewed_editorial_cuts,
)
from subtitler.errors import SubtitlerError


class EditorialReviewTests(unittest.TestCase):
    def test_review_copy_infers_neighboring_editorial_checkpoint_from_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "3.game.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions("Game", "Run", 5000, 9000),
            )
            artifact["editorial_map"].update(
                {"workflow": "human_information", "status": "complete"}
            )
            checkpoint = root / "3.game-editorial.json"
            original = root / "3.game.exo"
            reviewed = root / "manually-reviewed.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(original, artifact)
            reviewed.write_bytes(original.read_bytes())

            result = apply_reviewed_editorial_cuts(reviewed)
            self.assertTrue(checkpoint.samefile(Path(result["checkpoint"])))

    def test_empty_or_generated_narration_text_does_not_become_user_direction(self) -> None:
        self.assertTrue(_is_narration_text("[ナレーション]"))
        self.assertTrue(_is_narration_text("[ナレーション] この区間を要約"))
        self.assertTrue(_is_narration_text("【ナレーション】 この区間を要約"))
        self.assertEqual(_narration_direction("NARRATION"), "")
        self.assertEqual(_narration_direction("[ナレーション]"), "")
        self.assertEqual(
            _narration_direction("[ナレーション] この区間を要約"),
            "この区間を要約",
        )
        self.assertEqual(
            _narration_direction(
                "NARRATION\nWhat happens in this range:\n- A generated fact [N1]"
            ),
            "",
        )

    def test_wrapped_user_narration_direction_is_preserved(self) -> None:
        self.assertEqual(
            _narration_direction(
                "NARRATION\nExplain how this item\nchanges the boss strategy."
            ),
            "Explain how this item changes the boss strategy.",
        )

    def test_reviewed_narration_generates_factual_brief_and_timed_references(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.prompt = ""

            def complete_structured(
                self, prompt, *, max_tokens, operation, response_schema=None
            ):
                self.prompt = prompt
                self.assertEqual(operation, "editorial_narration_review")
                self.assertIsNotNone(response_schema)
                return '{"facts":[{"text":"The build changes before the boss.","evidence_ids":["candidate-0001"]}],"selected_candidate_ids":["candidate-0001"]}'

            def assertEqual(self, first, second) -> None:
                if first != second:
                    raise AssertionError(f"{first!r} != {second!r}")

            def assertIsNotNone(self, value) -> None:
                if value is None:
                    raise AssertionError("expected a structured response schema")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "run.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions("Game", "Run", 5000, 9000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["sources"][0]["result"] = {
                "event_graph": {"nodes": [{
                    "start_ms": 3200,
                    "end_ms": 4200,
                    "observed_label": "Changing the build",
                }]},
                "activity_episodes": [],
                "semantic_spans": [],
            }
            artifact["editorial_map"].update({
                "workflow": "human_information",
                "status": "complete",
                "final_actions": [{
                    "action_id": "narration-001",
                    "action_type": "narrated_summary",
                    "source_id": source_id,
                    "start_ms": 3000,
                    "end_ms": 7000,
                    "instruction": "Explain why this build matters.",
                }],
                "confirmed_cuts": [{
                    "source_id": source_id,
                    "start_ms": 3000,
                    "end_ms": 7000,
                    "candidate_kind": "voice_free_gap",
                }],
            })
            checkpoint = root / "run-editorial.json"
            reviewed = root / "run-editorial.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(reviewed, artifact)
            provider = Provider()

            def extract_frame(command, **_kwargs):
                Path(command[-1]).write_bytes(b"jpeg")
                return SimpleNamespace(returncode=0)

            with patch("subtitler.editorial_review.subprocess.run", extract_frame):
                result = apply_reviewed_editorial_cuts(
                    reviewed, narration_provider=provider
                )
            output = Path(result["output_path"]).read_text(encoding="shift_jis")
            report = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["cut_count"], 0)
        self.assertEqual(result["narration_brief_count"], 1)
        self.assertEqual(result["narration_reference_count"], 1)
        self.assertIn("Explain why this build matters.", provider.prompt)
        markers = _text_markers(output)
        narration = next(item for item in markers if item.text.startswith("NARRATION"))
        reference = next(item for item in markers if item.text.startswith("[N1]"))
        self.assertEqual(reference.layer, narration.layer - 1)
        self.assertNotIn("The build changes before the boss.", narration.text)
        self.assertIn("Explain why this build matters.", narration.text)
        self.assertIn("The build changes before the boss.", report)
        self.assertIn("N1", report)
        self.assertIn('class="narration-columns"', report)
        self.assertIn('class="events"', report)
        self.assertIn('class="references"', report)
        self.assertIn("<img", report)
        self.assertIn("00:00:03–00:00:04", report)
        self.assertNotRegex(report, r"\d{2}:\d{2}:\d{2}\.\d{3}")

    def test_moved_japanese_narration_uses_its_reviewed_range_for_the_brief(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "run.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions(
                    "Game", "Run", 5000, 9000, output_locale="ja"
                ),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["sources"][0]["result"] = {
                "event_graph": {"nodes": [{
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "semantic_label": "最初のイベント",
                    "semantic_summary": "元の提案範囲です。",
                }, {
                    "start_ms": 6000,
                    "end_ms": 7000,
                    "semantic_label": "移動後のイベント",
                    "semantic_summary": "ユーザーが選んだ実際の範囲です。",
                }]},
                "activity_episodes": [],
            }
            artifact["editorial_map"].update({
                "workflow": "human_information",
                "status": "complete",
                "final_actions": [{
                    "action_id": "narration-001",
                    "action_type": "narrated_summary",
                    "source_id": source_id,
                    "start_ms": 1000,
                    "end_ms": 2000,
                }],
                "confirmed_cuts": [],
            })
            checkpoint = root / "run-editorial.json"
            reviewed = root / "run-editorial.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(reviewed, artifact)
            text = reviewed.read_text(encoding="shift_jis")
            narration_hex = encode_text_for_exo("ナレーション")

            def move_narration(match: re.Match[str]) -> str:
                block = match.group(0)
                if narration_hex not in block:
                    return block
                block = re.sub(r"(?m)^start=\d+$", "start=361", block, count=1)
                return re.sub(r"(?m)^end=\d+$", "end=420", block, count=1)

            text = re.sub(r"(?ms)^\[\d+\]\n.*?(?=^\[\d+\]\n|\Z)", move_narration, text)
            text += (
                "[999]\nstart=301\nend=480\nlayer=20\noverlay=1\ncamera=0\n"
                "[999.0]\n_name=テキスト\n"
                f"text={encode_text_for_exo('[CUT]')}\n"
            )
            reviewed.write_text(text, encoding="shift_jis")

            result = apply_reviewed_editorial_cuts(reviewed)
            output = Path(result["output_path"]).read_text(encoding="shift_jis")

            report = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["cut_count"], 0)
        narration = _narration_markers(output)
        self.assertEqual(len(narration), 1)
        self.assertEqual(narration[0].text, "ナレーション")
        self.assertIn("移動後のイベント", report)
        self.assertNotIn("最初のイベント", report)

    def test_reviewed_marker_is_applied_to_paired_media_and_narration_survives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gameplay = root / "run.game.mp4"
            facecam = root / "run.face.mp4"
            gameplay.write_bytes(b"gameplay")
            facecam.write_bytes(b"facecam")
            artifact = create_editorial_project(
                [
                    EditorialSourceInput(
                        gameplay,
                        20_000,
                        audio_path=facecam,
                        visual_path=gameplay,
                        audio_duration_ms=20_000,
                        visual_duration_ms=20_000,
                        frame_rate=60,
                        width=1920,
                        height=1080,
                        audio_width=1280,
                        audio_height=720,
                        media_mode="paired",
                        pairing_basis="filename",
                    )
                ],
                EditorialProjectOptions("Game", "Run", 10_000, 18_000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["sources"][0]["result"] = {
                "utterance_groups": [
                    {"start_ms": 1000, "end_ms": 2500, "text": "Opening thought"}
                ]
            }
            artifact["editorial_map"].update(
                {
                    "workflow": "human_information",
                    "status": "complete",
                    "final_actions": [
                        {
                            "action_id": "narration-001",
                            "action_type": "narrated_summary",
                            "source_id": source_id,
                            "start_ms": 0,
                            "end_ms": 3000,
                        },
                        {
                            "action_id": "cut-0001",
                            "action_type": "cut",
                            "source_id": source_id,
                            "start_ms": 4000,
                            "end_ms": 7000,
                        },
                    ],
                    "confirmed_cuts": [
                        {
                            "source_id": source_id,
                            "start_ms": 4000,
                            "end_ms": 7000,
                            "candidate_kind": "unnecessary_speech",
                        }
                    ],
                    "removed_ms": 3000,
                }
            )
            checkpoint = root / "run-editorial.json"
            reviewed = root / "run-editorial.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(reviewed, artifact)
            text = reviewed.read_text(encoding="shift_jis")
            self.assertIn(encode_text_for_exo("[CUT]"), text)
            self.assertIn(encode_text_for_exo("Utterance 0001: Opening thought"), text)
            self.assertIn("Y=-405.0", text)
            text = text.replace(
                "start=241\nend=420\nlayer=6", "start=301\nend=540\nlayer=6"
            )
            text += (
                "[999]\nstart=181\nend=600\nlayer=20\noverlay=1\ncamera=0\n"
                "[999.0]\n_name=テキスト\n"
                f"text={encode_text_for_exo('USER EDIT')}\n"
            )
            reviewed.write_text(text, encoding="shift_jis")

            result = apply_reviewed_editorial_cuts(reviewed)
            output = Path(result["output_path"]).read_text(encoding="shift_jis")

        self.assertEqual(result["cut_count"], 1)
        self.assertEqual(result["removed_ms"], 4000)
        self.assertEqual(result["ignored_short_count"], 0)
        self.assertNotIn("text=" + ("0" * 4096), output)
        narration_markers = _narration_markers(output)
        self.assertEqual(len(narration_markers), 1)
        self.assertEqual(narration_markers[0].text, "NARRATION")
        for layer in range(1, 5):
            self.assertIn(f"layer={layer}", output)
        self.assertRegex(output, r"(?s)layer=4.*?file=.*run\.face\.mp4")
        self.assertIn("length=961", output)
        self.assertEqual(output.count(encode_text_for_exo("USER EDIT")), 2)
        self.assertRegex(output, r"(?s)start=181\nend=300\nlayer=20.*?USER EDIT".replace("USER EDIT", encode_text_for_exo("USER EDIT")))
        self.assertRegex(output, r"(?s)start=301\nend=360\nlayer=20.*?USER EDIT".replace("USER EDIT", encode_text_for_exo("USER EDIT")))

    def test_reviewed_marker_shorter_than_two_seconds_is_still_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "run.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions("Game", "Run", 5000, 9000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"].update(
                {
                    "workflow": "human_information",
                    "status": "complete",
                    "final_actions": [
                        {
                            "action_id": "cut-0001",
                            "action_type": "cut",
                            "source_id": source_id,
                            "start_ms": 4000,
                            "end_ms": 5000,
                        }
                    ],
                    "confirmed_cuts": [
                        {
                            "source_id": source_id,
                            "start_ms": 4000,
                            "end_ms": 5000,
                            "candidate_kind": "silence",
                        }
                    ],
                }
            )
            checkpoint = root / "run-editorial.json"
            reviewed = root / "run-editorial.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(reviewed, artifact)

            result = apply_reviewed_editorial_cuts(reviewed)

        self.assertEqual(result["cut_count"], 1)
        self.assertEqual(result["removed_ms"], 1000)
        self.assertEqual(result["ignored_short_count"], 0)

    def test_reviewed_narration_invalidates_its_initial_overlapping_cut_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "run.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions("Game", "Run", 5000, 9000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["sources"][0]["result"] = {
                "event_graph": {"nodes": [{
                    "start_ms": 3500,
                    "end_ms": 5000,
                    "semantic_label": "Build selected",
                    "semantic_summary": "The player chooses a healing-focused build.",
                }]},
                "activity_episodes": [],
            }
            artifact["editorial_map"].update(
                {
                    "workflow": "human_information",
                    "status": "complete",
                    "final_actions": [
                        {
                            "action_id": "narration-001",
                            "action_type": "narrated_summary",
                            "source_id": source_id,
                            "start_ms": 3000,
                            "end_ms": 7000,
                        }
                    ],
                    "confirmed_cuts": [
                        {
                            "source_id": source_id,
                            "start_ms": 3000,
                            "end_ms": 7000,
                            "candidate_kind": "silence",
                        }
                    ],
                }
            )
            checkpoint = root / "run-editorial.json"
            reviewed = root / "run-editorial.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(reviewed, artifact)
            initial = reviewed.read_text(encoding="shift_jis")
            self.assertIn(encode_text_for_exo("[CUT]"), initial)

            result = apply_reviewed_editorial_cuts(reviewed)
            output = Path(result["output_path"]).read_text(encoding="shift_jis")
            report = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["cut_count"], 0)
        self.assertEqual(result["removed_ms"], 0)
        self.assertIn("length=601", output)
        narration_markers = _narration_markers(output)
        self.assertEqual(len(narration_markers), 1)
        self.assertNotIn("Build selected", narration_markers[0].text)
        self.assertIn("Build selected", report)
        self.assertNotIn("healing-focused build", report)

    def test_deleting_narration_preserves_and_applies_initial_cut(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "run.mp4"
            media.write_bytes(b"media")
            artifact = create_editorial_project(
                [EditorialSourceInput(media, 10_000, frame_rate=60)],
                EditorialProjectOptions("Game", "Run", 5000, 9000),
            )
            source_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"].update({
                "workflow": "human_information",
                "status": "complete",
                "final_actions": [{
                    "action_id": "narration-001",
                    "action_type": "narrated_summary",
                    "source_id": source_id,
                    "start_ms": 3000,
                    "end_ms": 7000,
                }],
                "confirmed_cuts": [{
                    "source_id": source_id,
                    "start_ms": 3000,
                    "end_ms": 7000,
                    "candidate_kind": "voice_free_gap",
                }],
            })
            checkpoint = root / "run-editorial.json"
            reviewed = root / "run-editorial.exo"
            write_editorial_checkpoint(checkpoint, artifact)
            write_editorial_exo(reviewed, artifact)
            text = reviewed.read_text(encoding="shift_jis")
            narration_index = _narration_markers(text)[0].object_index
            text = re.sub(
                rf"(?ms)^\[{narration_index}\]\n.*?(?=^\[\d+\]\n|\Z)",
                "",
                text,
            )
            reviewed.write_text(text, encoding="shift_jis")

            result = apply_reviewed_editorial_cuts(reviewed)

        self.assertEqual(result["cut_count"], 1)
        self.assertEqual(result["removed_ms"], 4000)
        self.assertEqual(result["narration_brief_count"], 0)

    def test_reviewed_marker_crossing_sources_is_split_and_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "part-1.mp4"
            second = root / "part-2.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            artifact = create_editorial_project(
                [
                    EditorialSourceInput(first, 10_000, frame_rate=60),
                    EditorialSourceInput(second, 10_000, frame_rate=60),
                ],
                EditorialProjectOptions("Game", "Run", 10_000, 18_000),
            )
            first_id = artifact["sources"][0]["source_id"]
            artifact["editorial_map"].update(
                {
                    "workflow": "cutting_assistant",
                    "status": "complete",
                    "final_actions": [],
                    "confirmed_cuts": [
                        {
                            "source_id": first_id,
                            "start_ms": 8000,
                            "end_ms": 10_000,
                            "candidate_kind": "silence",
                        }
                    ],
                }
            )
            cuts, ignored_short = _map_markers_to_sources(
                [(481, 720)], fps=60.0, project=artifact
            )

        self.assertEqual(len(cuts), 2)
        self.assertEqual(
            sum(item["end_ms"] - item["start_ms"] for item in cuts), 4000
        )
        self.assertEqual(ignored_short, 0)

    def test_aup_reports_export_requirement_without_parsing_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "review.aup"
            project.write_bytes(b"AviUtl ProjectFile version 0.18\0binary")
            with self.assertRaisesRegex(SubtitlerError, "Export.*EXO"):
                apply_reviewed_editorial_cuts(project)


if __name__ == "__main__":
    unittest.main()
