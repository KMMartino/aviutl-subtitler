import io
import tempfile
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.editorial_analysis import EDITORIAL_PROMPT_VERSION, TranscriptEvidence
from subtitler.editorial_hosted import (
    HostedEditorialExecutorOptions,
    HostedEditorialStageExecutor,
    _analyze_editorial_visual_windows,
    _load_transcript_evidence,
    _align_emphasized_phrases,
    _clean_selected_editorial_subtitles,
    _load_semantic_progress,
    _load_vad_speech_activity,
    _tighten_transcript_to_speech_activity,
)
from subtitler.editorial_project import EDITORIAL_STAGE_VERSIONS
from subtitler.errors import SubtitlerError
from subtitler.media_analysis import MediaAnalysisResponseError, MediaAnalysisResult


class HostedEditorialTests(unittest.TestCase):
    def test_editorial_transcript_edges_are_tightened_to_fine_vad(self) -> None:
        transcript = [TranscriptEvidence(190_024, 195_144, "いや、お前も悪いやつだろ。")]

        tightened = _tighten_transcript_to_speech_activity(
            transcript,
            [(187_704, 189_224), (191_128, 193_192)],
        )

        self.assertEqual(
            [(item.start_ms, item.end_ms, item.text) for item in tightened],
            [(191_128, 193_192, "いや、お前も悪いやつだろ。")],
        )

    def test_editorial_transcript_inside_continuous_vad_keeps_its_alignment(self) -> None:
        transcript = [TranscriptEvidence(10_000, 12_000, "continuous speech")]

        tightened = _tighten_transcript_to_speech_activity(
            transcript,
            [(9_000, 13_000)],
        )

        self.assertEqual(tightened, transcript)

    def test_cached_late_punctuation_is_attached_without_extending_utterance(self) -> None:
        transcript = [
            TranscriptEvidence(7_060_600, 7_061_384, "ナイス"),
            TranscriptEvidence(7_064_618, 7_064_698, "。"),
        ]

        tightened = _tighten_transcript_to_speech_activity(
            transcript,
            [(7_060_600, 7_061_384)],
        )

        self.assertEqual(
            tightened,
            [TranscriptEvidence(7_060_600, 7_061_384, "ナイス。")],
        )

    def test_spoken_continuation_after_thinking_pause_is_not_collapsed(self) -> None:
        transcript = [
            TranscriptEvidence(1_000, 2_000, "どうしようかな、"),
            TranscriptEvidence(7_000, 8_000, "こっちにしよう。"),
        ]

        tightened = _tighten_transcript_to_speech_activity(
            transcript,
            [(1_000, 2_000), (7_000, 8_000)],
        )

        self.assertEqual(tightened, transcript)

    def test_vad_speech_activity_loader_uses_selected_fine_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vad.csv"
            path.write_text(
                "start,end,selected_for_transcription\n"
                "1.128,3.192,true\n"
                "4.000,5.000,false\n",
                encoding="utf-8",
            )

            activity = _load_vad_speech_activity(path)

        self.assertEqual(activity, [(1_128, 3_192)])

    def test_semantic_progress_is_invalidated_when_transcription_version_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "semantic-progress.json"
            path.write_text(
                json.dumps({
                    "semantic_stage_version": EDITORIAL_STAGE_VERSIONS["semantic_spans"],
                    "visual_stage_version": EDITORIAL_STAGE_VERSIONS["visual_learning"],
                    "prompt_version": EDITORIAL_PROMPT_VERSION,
                    "source_id": "source-1",
                    "source_duration_ms": 10_000,
                    "completed_windows": [{"base_window_index": 0}],
                }),
                encoding="utf-8",
            )

            loaded = _load_semantic_progress(
                path, source_id="source-1", source_duration_ms=10_000
            )

        self.assertEqual(loaded["completed_windows"], [])
        self.assertEqual(
            loaded["transcription_stage_version"],
            EDITORIAL_STAGE_VERSIONS["transcription"],
        )

    def test_long_visual_learning_uses_bounded_dense_windows(self) -> None:
        analysis = MediaAnalysisResult(
            "Gameplay", [], [], "openai", "model", "v1", 10, 100, 20, 0.01
        )
        with (
            patch("subtitler.editorial_hosted.OpenAIEditorialVisualProvider"),
            patch(
                "subtitler.editorial_hosted.analyze_media", return_value=analysis
            ) as analyze,
        ):
            result = _analyze_editorial_visual_windows(
                media_path=Path("game.mp4"),
                duration_sec=31 * 60,
                detail="detailed",
                ffmpeg="ffmpeg",
                sampling_scale=1.5,
                model="gpt-5.6-luna",
                reasoning_effort="low",
                output_locale="ja",
                editorial_context="context",
            )

        self.assertEqual(analyze.call_count, 3)
        windows = sorted(
            (call.kwargs["start_sec"], call.kwargs["end_sec"])
            for call in analyze.call_args_list
        )
        self.assertEqual(windows, [(0.0, 720.0), (720.0, 1440.0), (1440.0, 1860)])
        self.assertEqual(result.sample_count, 30)

    def test_visual_learning_reuses_each_completed_window(self) -> None:
        analysis = MediaAnalysisResult(
            "Gameplay", [], [], "openai", "model", "v1", 10, 100, 20, 0.01
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "subtitler.editorial_hosted.OpenAIEditorialVisualProvider"
        ), patch(
            "subtitler.editorial_hosted.analyze_media", return_value=analysis
        ) as analyze:
            progress_path = Path(directory) / "visual-progress.json"
            arguments = {
                "media_path": Path("game.mp4"),
                "duration_sec": 20 * 60,
                "detail": "detailed",
                "ffmpeg": "ffmpeg",
                "sampling_scale": 1.5,
                "model": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "output_locale": "ja",
                "editorial_context": "context",
                "progress_path": progress_path,
            }
            first = _analyze_editorial_visual_windows(**arguments)
            second = _analyze_editorial_visual_windows(**arguments)

        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(first.sample_count, 20)
        self.assertEqual(second.sample_count, 20)

    def test_cached_visual_windows_skip_request_pacing(self) -> None:
        analysis = MediaAnalysisResult(
            "Gameplay", [], [], "openai", "model", "v1", 10, 100, 20, 0.01
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "subtitler.editorial_hosted.OpenAIEditorialVisualProvider"
        ), patch(
            "subtitler.editorial_hosted.analyze_media", return_value=analysis
        ), patch("subtitler.editorial_hosted.time.sleep") as sleep:
            progress_path = Path(directory) / "visual-progress.json"
            arguments = {
                "media_path": Path("game.mp4"),
                "duration_sec": 20 * 60,
                "detail": "detailed",
                "ffmpeg": "ffmpeg",
                "sampling_scale": 1.5,
                "model": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "output_locale": "ja",
                "editorial_context": "context",
                "progress_path": progress_path,
                "max_workers": 1,
            }
            _analyze_editorial_visual_windows(**arguments)
            _analyze_editorial_visual_windows(**arguments, window_interval_sec=30.0)

        sleep.assert_not_called()

    def test_malformed_visual_window_is_retried_as_smaller_requests(self) -> None:
        recovered = MediaAnalysisResult(
            "Recovered", [], [], "openai", "model", "v1", 1, 10, 2, 0.01
        )
        with patch(
            "subtitler.editorial_hosted.OpenAIEditorialVisualProvider"
        ), patch(
            "subtitler.editorial_hosted.analyze_media",
            side_effect=[MediaAnalysisResponseError("malformed"), recovered, recovered],
        ) as analyze:
            result = _analyze_editorial_visual_windows(
                media_path=Path("game.mp4"),
                duration_sec=10 * 60,
                detail="detailed",
                ffmpeg="ffmpeg",
                sampling_scale=1.5,
                model="gpt-5.6-luna",
                reasoning_effort="low",
                output_locale="ja",
                editorial_context="context",
            )

        self.assertEqual(analyze.call_count, 3)
        self.assertEqual(result.sample_count, 2)

    def test_visual_window_failure_does_not_start_queued_windows(self) -> None:
        with patch(
            "subtitler.editorial_hosted.OpenAIEditorialVisualProvider"
        ), patch(
            "subtitler.editorial_hosted.analyze_media",
            side_effect=SubtitlerError("rate limited"),
        ) as analyze:
            with self.assertRaisesRegex(SubtitlerError, "rate limited"):
                _analyze_editorial_visual_windows(
                    media_path=Path("game.mp4"),
                    duration_sec=31 * 60,
                    detail="detailed",
                    ffmpeg="ffmpeg",
                    sampling_scale=1.5,
                    model="gpt-5.6-luna",
                    reasoning_effort="low",
                    output_locale="ja",
                    editorial_context="context",
                    max_workers=1,
                )
        self.assertEqual(analyze.call_count, 1)

    def test_editorial_and_subtitle_cleanup_models_are_independent(self) -> None:
        executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
        executor.config = {
            "cleanup": {"backend": "openai", "api_model": "user-cleanup"},
            "editorial": {
                "analysis_model": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "director_model": "gpt-5.6-terra",
                "director_reasoning_effort": "low",
                "subtitle_cleanup_model": "gpt-5.6-luna",
                "subtitle_cleanup_reasoning_effort": "low",
            },
        }

        editorial = executor._editorial_model_config()
        director = executor._director_model_config()
        cleanup = executor._subtitle_cleanup_model_config()

        self.assertEqual(editorial["cleanup"]["api_model"], "gpt-5.6-luna")
        self.assertEqual(editorial["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(director["cleanup"]["api_model"], "gpt-5.6-terra")
        self.assertEqual(director["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(cleanup["cleanup"]["api_model"], "gpt-5.6-luna")
        self.assertEqual(cleanup["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(executor.config["cleanup"]["api_model"], "user-cleanup")

    def test_editorial_analysis_defaults_to_medium_reasoning(self) -> None:
        executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
        executor.config = {"editorial": {}}

        self.assertEqual(
            executor._editorial_model_config()["cleanup"]["reasoning_effort"],
            "medium",
        )

    def test_cutting_assistant_selects_aligns_and_cleans_sparse_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=root / "workspace",
                pipeline_script=root / "pipeline.py",
            )
            executor.config = {"cleanup": {}, "editorial": {}}
            project = {
                "output_locale": "en",
                "title_or_game": "Game",
                "objective": "Explain the run",
                "target_duration_min_ms": 1_000,
                "target_duration_max_ms": 2_000,
                "editorial_map": {
                    "global_reconciliation": {"output": {"global_threads": []}},
                    "action_planning": {"output": None},
                },
                "sources": [{
                    "source_id": "source-1",
                    "stages": {"transcription": {"output": {
                        "aligned_tokens_path": str(root / "tokens.csv")
                    }}},
                }],
            }
            planner = Mock()
            cleaner = Mock()
            cleaner.refine.return_value = ["Cleaned line"]
            with (
                patch.object(executor, "_build_editorial_refiner", return_value=planner),
                patch.object(executor, "_build_subtitle_cleanup_refiner", return_value=cleaner) as cleanup,
                patch("subtitler.editorial_hosted.select_editorial_subtitles", return_value=[{
                    "source_id": "source-1", "start_ms": 100, "end_ms": 500,
                    "source_text": "Raw line", "reason": "Reaction",
                    "emphasis_energy": 0.5, "confidence": 0.9,
                }]),
                patch("subtitler.editorial_hosted._align_emphasized_phrases", return_value=[{
                    "source_id": "source-1", "start_ms": 120, "end_ms": 480,
                    "source_text": "Raw line", "text": "Raw line",
                    "timing_verified": True,
                }]),
            ):
                result = executor.plan_actions(project)

        cleanup.assert_called_once()
        cleaner.refine.assert_called_once_with(["Raw line"])
        cleaner.close.assert_called_once_with()
        self.assertEqual(result["emphasized_phrases"][0]["text"], "Cleaned line")
        self.assertTrue(result["emphasized_phrases"][0]["cleanup_applied"])
        self.assertEqual(result["workflow"], "human_information")

    def test_action_planning_requires_completed_factual_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=root / "workspace",
                pipeline_script=root / "pipeline.py",
            )
            executor.config = {"cleanup": {}, "editorial": {}}
            project = {
                "title_or_game": "Game",
                "objective": "Explain the run",
                "target_duration_min_ms": 1_000,
                "target_duration_max_ms": 2_000,
                "must_keep_notes": [],
                "de_emphasize_notes": [],
                "editorial_map": {
                    "global_reconciliation": {"output": None},
                    "action_planning": {"output": None},
                },
                "sources": [],
            }
            with self.assertRaisesRegex(SubtitlerError, "completed story synthesis"):
                executor.plan_actions(project)

    def test_global_synthesis_reports_progress_while_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=root / "workspace",
                pipeline_script=root / "pipeline.py",
            )
            executor.config = {"cleanup": {}, "editorial": {}}
            project = {
                "output_locale": "en",
                "title_or_game": "Game",
                "objective": "Explain",
                "target_duration_min_ms": 1_000,
                "target_duration_max_ms": 2_000,
                "must_keep_notes": [],
                "de_emphasize_notes": [],
                "editorial_map": {
                    "global_reconciliation": {"output": None},
                    "action_planning": {"output": None},
                },
                "sources": [],
            }
            base = {"global_threads": [], "conflicts": []}
            refiner = Mock()

            def slow_result(value: dict[str, object]) -> dict[str, object]:
                time.sleep(0.035)
                return value

            output = io.StringIO()
            with (
                patch.object(executor, "_build_editorial_refiner", return_value=refiner),
                patch.object(executor, "_build_director_refiner", return_value=refiner),
                patch(
                    "subtitler.editorial_hosted.synthesize_human_information_project",
                    side_effect=lambda **_kwargs: slow_result(base),
                ),
                patch("subtitler.editorial_hosted.EDITORIAL_PROGRESS_FIRST_UPDATE_SECONDS", 0.01),
                patch("subtitler.editorial_hosted.EDITORIAL_PROGRESS_UPDATE_INTERVAL_SECONDS", 0.02),
                redirect_stdout(output),
            ):
                executor.finalize_project(project)

            logs = output.getvalue()
            self.assertIn("Story synthesis: the hosted model is still processing", logs)

    def test_emphasized_phrase_uses_verified_token_timing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.csv"
            path.write_text(
                "chunk_index,token_index,start,end,text,kind\n"
                "0,0,1.0,1.2,I'm,word\n"
                "0,1,1.2,1.5,cooked,word\n",
                encoding="utf-8",
            )
            result = _align_emphasized_phrases(
                [{"id": "e", "source_text": "I'm cooked", "start_ms": 900, "end_ms": 2000}],
                path,
            )
        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (1000, 1500))
        self.assertTrue(result[0]["timing_verified"])

    def test_long_selected_phrase_becomes_short_token_timed_display_beats(self) -> None:
        words = [
            "This", "is", "a", "surprisingly", "important", "discovery,",
            "and", "now", "we", "run.",
        ]
        source_text = " ".join(words)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.csv"
            rows = ["chunk_index,token_index,start,end,text,kind"]
            for index, word in enumerate(words):
                csv_word = f'"{word}"' if "," in word else word
                rows.append(
                    f"0,{index},{1 + index * 0.2:.1f},{1.2 + index * 0.2:.1f},{csv_word},word"
                )
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            result = _align_emphasized_phrases(
                [{
                    "id": "phrase",
                    "source_text": source_text,
                    "start_ms": 900,
                    "end_ms": 4000,
                }],
                path,
            )

        self.assertGreater(len(result), 1)
        self.assertTrue(
            all(len("".join(item["text"].split())) <= 20 for item in result)
        )
        self.assertEqual(
            "".join("".join(item["text"].split()) for item in result),
            "".join(source_text.split()),
        )
        self.assertEqual(
            [item["display_segment_index"] for item in result],
            list(range(1, len(result) + 1)),
        )
        self.assertTrue(
            all(
                int(left["end_ms"]) <= int(right["start_ms"])
                for left, right in zip(result, result[1:])
            )
        )

    def test_selected_editorial_subtitles_are_cleaned_without_changing_timing(self) -> None:
        refiner = Mock()
        refiner.refine.return_value = ["Cleaned\nphrase。"]
        phrase = {
            "source_id": "source-1",
            "start_ms": 1_000,
            "end_ms": 1_500,
            "source_text": "raw phrase",
            "text": "raw phrase",
            "timing_verified": True,
        }

        result = _clean_selected_editorial_subtitles([phrase], refiner)

        refiner.refine.assert_called_once_with(["raw phrase"])
        self.assertEqual(result[0]["text"], "Cleaned phrase。")
        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (1_000, 1_500))
        self.assertEqual(result[0]["source_text"], "raw phrase")
        self.assertTrue(result[0]["cleanup_applied"])

    def test_emphasized_phrase_clamps_silence_stretched_boundary_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.csv"
            path.write_text(
                "chunk_index,token_index,start,end,text,kind\n"
                "0,0,1.0,2.5,A,char\n"
                "0,1,2.5,5.0,B,char\n"
                "0,2,5.0,5.0,!,char\n",
                encoding="utf-8",
            )
            result = _align_emphasized_phrases(
                [{"id": "e", "source_text": "AB!", "start_ms": 900, "end_ms": 6000}],
                path,
            )

        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (1750, 3250))

    def test_emphasized_phrase_rejects_internal_silence_stretch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.csv"
            path.write_text(
                "chunk_index,token_index,start,end,text,kind\n"
                "0,0,1.0,1.2,A,char\n"
                "0,1,1.2,4.0,B,char\n"
                "0,2,4.0,4.2,C,char\n",
                encoding="utf-8",
            )
            result = _align_emphasized_phrases(
                [{"id": "e", "source_text": "ABC", "start_ms": 900, "end_ms": 5000}],
                path,
            )

        self.assertEqual(result, [])

    def test_emphasized_phrase_deduplicates_punctuation_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.csv"
            path.write_text(
                "chunk_index,token_index,start,end,text,kind\n"
                "0,0,1.0,1.2,Good,word\n"
                "0,1,1.2,1.5,news,word\n"
                "0,2,1.5,1.5,!,char\n",
                encoding="utf-8",
            )
            result = _align_emphasized_phrases(
                [
                    {"id": "short", "source_text": "Good news", "start_ms": 900, "end_ms": 2000, "confidence": 0.9},
                    {"id": "punctuated", "source_text": "Good news!", "start_ms": 900, "end_ms": 2000, "confidence": 0.9},
                ],
                path,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "punctuated")

    def test_loads_cleaned_text_with_matching_timing_as_semantic_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timing = root / "timing.csv"
            text = root / "text.txt"
            timing.write_text(
                "subtitle_index,start,end\n0,1.25,2.5\n1,3,4.125\n",
                encoding="utf-8",
            )
            text.write_text("1. First observation\n2. Second observation\n", encoding="utf-8")

            evidence = _load_transcript_evidence(timing, text)

            self.assertEqual(
                [(item.start_ms, item.end_ms, item.text) for item in evidence],
                [(1250, 2500, "First observation"), (3000, 4125, "Second observation")],
            )

    def test_rejects_mismatched_transcript_artifacts_instead_of_misaligning_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timing = root / "timing.csv"
            text = root / "text.txt"
            timing.write_text("subtitle_index,start,end\n0,1,2\n", encoding="utf-8")
            text.write_text("1. First\n2. Extra\n", encoding="utf-8")

            with self.assertRaises(SubtitlerError):
                _load_transcript_evidence(timing, text)

    def test_probe_validates_pair_sync_using_gameplay_frame_rate(self) -> None:
        executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
        source = {
            "source_id": "source-1",
            "original_name": "run-gameplay.mp4",
            "media_mode": "paired",
            "audio_path": "run-facecam.mp4",
            "visual_path": "run-gameplay.mp4",
            "audio_original_name": "run-facecam.mp4",
            "visual_original_name": "run-gameplay.mp4",
            "audio_duration_ms": 60_100,
            "visual_duration_ms": 60_000,
            "frame_rate": 60.0,
        }
        with (
            patch("subtitler.editorial_hosted.get_media_duration", side_effect=[60.1, 60.0]),
            patch("subtitler.editorial_hosted._probe_frame_rate", return_value=60.0),
        ):
            result = executor._probe(source)
        self.assertEqual(result["audio_path"], "run-facecam.mp4")
        self.assertEqual(result["visual_path"], "run-gameplay.mp4")

        with (
            patch("subtitler.editorial_hosted.get_media_duration", side_effect=[60.2, 60.0]),
            patch("subtitler.editorial_hosted._probe_frame_rate", return_value=60.0),
            self.assertRaisesRegex(SubtitlerError, "more than 10 gameplay frames"),
        ):
            executor._probe(source)

    def test_paired_stages_transcribe_facecam_and_analyze_gameplay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            source_workspace = workspace / "source-1"
            source_workspace.mkdir(parents=True)
            (source_workspace / "transcript.subtitle_timing.csv").write_text(
                "subtitle_index,start,end\n0,1,2\n", encoding="utf-8"
            )
            (source_workspace / "transcript.final_text.txt").write_text(
                "1. Voice line\n", encoding="utf-8"
            )
            (source_workspace / "transcript.run.json").write_text(
                json.dumps({"backend": {"diagnostics": []}}), encoding="utf-8"
            )
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=workspace,
                pipeline_script=root / "aviutl_subtitle.py",
            )
            executor.config = {"editorial": {"visual_detail": "simple"}}
            source = {
                "source_id": "source-1",
                "original_name": "run-gameplay.mp4",
                "audio_path": str(root / "run-facecam.mp4"),
                "visual_path": str(root / "run-gameplay.mp4"),
            }
            process = Mock()
            process.stdout = []
            process.wait.return_value = 0
            with patch("subtitler.editorial_hosted.subprocess.Popen", return_value=process) as popen:
                executor._transcribe(source)
            command = popen.call_args.args[0]
            self.assertEqual(command[2], source["audio_path"])

            analysis = MediaAnalysisResult("Gameplay", [], [], "openai", "model", "v1", 1, 1, 1, 0.01)
            refiner = Mock()
            refiner.complete_structured.return_value = json.dumps({})
            with (
                patch("subtitler.editorial_hosted.OpenAIEditorialVisualProvider"),
                patch("subtitler.editorial_hosted.analyze_media", return_value=analysis) as analyze,
                patch("subtitler.editorial_hosted.analyze_acoustic_emphasis", return_value=[]),
                patch("subtitler.editorial_hosted.lookup_game_wiki", return_value={"status": "unavailable"}),
                patch("subtitler.editorial_hosted.build_refiner", return_value=refiner),
            ):
                executor._analyze_visuals(
                    source,
                    {"title_or_game": "Test game", "objective": "Finish the run"},
                    {"source_probe": {"duration_ms": 60_000}},
                )
            self.assertEqual(analyze.call_args.kwargs["media_path"], Path(source["visual_path"]))
            self.assertEqual(analyze.call_args.kwargs["sampling_scale"], 1.5)

    def test_rejects_transcription_artifacts_with_unresolved_audio_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_workspace = root / "workspace" / "source-1"
            source_workspace.mkdir(parents=True)
            (source_workspace / "transcript.subtitle_timing.csv").write_text(
                "subtitle_index,start,end\n0,1,2\n", encoding="utf-8"
            )
            (source_workspace / "transcript.final_text.txt").write_text("1. Partial\n", encoding="utf-8")
            (source_workspace / "transcript.run.json").write_text(json.dumps({
                "backend": {"diagnostics": [{"code": "transcription_failed", "region_index": 3}]}
            }), encoding="utf-8")
            (source_workspace / "transcript.vad_groups.csv").write_text(
                "chunk_index,start,end\n3,600,900\n", encoding="utf-8"
            )
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=root / "workspace",
                pipeline_script=root / "aviutl_subtitle.py",
            )
            source = {
                "source_id": "source-1",
                "original_name": "run.mp4",
                "audio_path": str(root / "run.mp4"),
            }
            process = Mock(stdout=[])
            process.wait.return_value = 0

            with (
                patch("subtitler.editorial_hosted.subprocess.Popen", return_value=process),
                self.assertRaisesRegex(SubtitlerError, "10.0-15.0 min"),
            ):
                executor._transcribe(source)


if __name__ == "__main__":
    unittest.main()
