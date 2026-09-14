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
    _compact_reviewed_exo,
    _map_markers_to_sources,
    _is_narration_text,
    _narration_direction,
    _narration_markers,
    _text_markers,
    apply_reviewed_editorial_cuts,
)
from subtitler.errors import SubtitlerError


class EditorialReviewTests(unittest.TestCase):
    def test_compaction_keeps_all_layers_and_source_positions_synchronized(self) -> None:
        # Combined recordings and paired sources use the same timeline transform.
        # Independent audio is seconds; video positions are source frames.
        for paired in (False, True):
            with self.subTest(paired=paired):
                objects = []
                def add(start: int, end: int, layer: int, filters: str, group: int = 7) -> None:
                    i = len(objects)
                    objects.append(f"[{i}]\nstart={start}\nend={end}\nlayer={layer}\ngroup={group}\n[{i}.0]\n{filters}\n")
                add(1, 300, 1, "_name=動画ファイル\n再生位置=61\n再生速度=50.0\nfile=game.mkv")
                add(1, 300, 2, "_name=音声ファイル\n再生位置=2.0\n再生速度=50.0\n動画ファイルと連携=0\nfile=game.wav")
                if paired:
                    add(1, 300, 3, "_name=動画ファイル\n再生位置=61\n再生速度=50.0\nfile=face.mkv")
                add(1, 300, 4, "_name=音声ファイル\n再生位置=5.25\n再生速度=50.0\n動画ファイルと連携=0\nfile=voice.wav")
                for layer, label in enumerate(('subtitle', 'QA', 'chapter', 'NARRATION', 'custom'), 5):
                    add(40, 210, layer, f"_name=テキスト\ntext={encode_text_for_exo(label)}\ncustom_filter_payload=unchanged")
                add(1, 20, 10, "_name=未知フィルタ\nfile=before.bin")
                add(200, 240, 11, "_name=未知フィルタ\nfile=after.bin")
                # This grouped peer starts after the first cut: its first piece
                # must join the media's second surviving piece, not its first.
                add(100, 110, 12, "_name=未知フィルタ\nfile=late-overlay.bin")
                original = "[exedit]\nrate=30\nscale=1\nlength=300\n" + ''.join(objects)
                result = _compact_reviewed_exo(original, [(61, 90), (151, 180)])
                parsed = [(int(re.search(r'^layer=(\d+)$', b, re.M).group(1)), b)
                          for b in re.findall(r'(?ms)^\[\d+\]\n(.*?)(?=^\[\d+\]\n|\Z)', result)]
                def bodies(layer: int) -> list[str]:
                    return [b for n, b in parsed if n == layer]
                def spans(layer: int) -> list[tuple[int, int]]:
                    return [(int(re.search(r'^start=(\d+)$', b, re.M).group(1)),
                             int(re.search(r'^end=(\d+)$', b, re.M).group(1))) for b in bodies(layer)]
                for layer in ([1, 2, 3, 4] if paired else [1, 2, 4]):
                    self.assertEqual(spans(layer), [(1, 60), (61, 120), (121, 240)])
                self.assertEqual([re.search(r'^再生位置=(.*)$', b, re.M).group(1) for b in bodies(1)], ['61', '106', '151'])
                self.assertEqual([float(re.search(r'^再生位置=(.*)$', b, re.M).group(1)) for b in bodies(2)], [2, 3.5, 5])
                self.assertEqual([float(re.search(r'^再生位置=(.*)$', b, re.M).group(1)) for b in bodies(4)], [5.25, 6.75, 8.25])
                for layer in range(5, 10):
                    self.assertEqual(spans(layer), [(40, 60), (61, 120), (121, 150)])
                    self.assertTrue(all('custom_filter_payload=unchanged' in b for b in bodies(layer)))
                self.assertEqual(spans(10), [(1, 20)])
                self.assertEqual(spans(11), [(140, 180)])
                self.assertEqual(spans(12), [(70, 80)])
                def group(body: str) -> str:
                    return re.search(r'^group=(\d+)$', body, re.M).group(1)
                self.assertEqual(group(bodies(12)[0]), group(bodies(1)[1]))
                self.assertIn('length=240', result)
                self.assertEqual(result.count('file=voice.wav'), 3)

    def test_compaction_uses_fractional_fps_and_keeps_linked_audio_linked(self) -> None:
        text = ('[exedit]\nrate=30000\nscale=1001\nlength=200\n'
                '[0]\nstart=1\nend=200\nlayer=2\n[0.0]\n_name=音声ファイル\n'
                '再生位置=1.25\n再生速度=100.0\n動画ファイルと連携=0\nfile=voice.wav\n'
                '[1]\nstart=1\nend=200\nlayer=4\n[1.0]\n_name=音声ファイル\n'
                '再生位置=0.00\n再生速度=100.0\n動画ファイルと連携=1\nfile=linked.mp4\n')
        result = _compact_reviewed_exo(text, [(31, 60)])
        self.assertIn('再生位置=3.252000', result)
        self.assertEqual(result.count('再生位置=0.00'), 2)
        self.assertEqual(result.count('動画ファイルと連携=1'), 2)
        with self.assertRaisesRegex(SubtitlerError, 'animated or invalid'):
            _compact_reviewed_exo(text.replace('再生速度=100.0', '再生速度=100.0,200.0,1'), [(31, 60)])

    def test_compaction_detaches_orphan_midpoint_chains_without_dropping_filters(self) -> None:
        text = '[exedit]\nrate=30\nscale=1\nlength=120\n'
        for i in range(4):
            chain = 'chain=1\n' if i else ''
            text += (f'[{i}]\nstart={i*30+1}\nend={(i+1)*30}\nlayer=9\n{chain}'
                     f'[{i}.0]\n_name=未知フィルタ\nX=1.0,2.0,3\nopaque_payload=keep\n')
        result = _compact_reviewed_exo(text, [(1, 30), (101, 110)])
        bodies = re.findall(r'(?ms)^\[\d+\]\n(.*?)(?=^\[\d+\]\n|\Z)', result)
        self.assertEqual(len(bodies), 4)
        self.assertNotIn('chain=1', bodies[0])  # Original parent was removed.
        self.assertIn('chain=1', bodies[1])
        self.assertIn('chain=1', bodies[2])
        self.assertNotIn('chain=1', bodies[3])  # Second surviving split is independent.
        self.assertTrue(all('X=1.0,2.0,3\nopaque_payload=keep' in b for b in bodies))

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
            # Updated collection artifacts, with no legacy event graph, must feed
            # both the factual context and the representative footage selection.
            artifact['sources'][0]['result'] = {}
            artifact['editorial_map']['editor_recommendations'] = {
                'catalog': [{'source_id': source_id, 'name': 'Run',
                    'activities': [{'activity_id': 'a1', 'start_ms': 3000, 'end_ms': 7000,
                                    'label': 'Prepare for boss', 'summary': 'Changed equipment'}],
                    'states': [{'state_id': 's1', 'activity_id': 'a1', 'start_ms': 3200, 'end_ms': 4200,
                                'observations': ['Changing the build']}],
                    'speech': [{'evidence_id': 'u1', 'start_ms': 3300, 'end_ms': 4000,
                                'text': 'I need more defense for this boss.'}]}], 'assessments': []}
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
                    reviewed, narration_provider=provider, provider_parameters={"model": "test"}
                )
                with patch.object(provider, "complete_structured", side_effect=AssertionError("paid review repeated")):
                    self.assertEqual(apply_reviewed_editorial_cuts(
                        reviewed, narration_provider=provider, provider_parameters={"model": "test"}), result)
                with patch.object(provider, "complete_structured", wraps=provider.complete_structured) as changed:
                    apply_reviewed_editorial_cuts(reviewed, narration_provider=provider,
                                                 provider_parameters={"model": "changed"})
                    changed.assert_called_once()
            output = Path(result["output_path"]).read_text(encoding="shift_jis")
            report = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["cut_count"], 0)
        self.assertEqual(result["narration_brief_count"], 1)
        self.assertEqual(result["narration_reference_count"], 1)
        self.assertIn("Explain why this build matters.", provider.prompt)
        self.assertIn("I need more defense for this boss.", provider.prompt)
        self.assertIn("Changing the build", provider.prompt)
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
