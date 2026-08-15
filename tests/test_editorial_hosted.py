import io
import tempfile
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.editorial_hosted import (
    HostedEditorialExecutorOptions,
    HostedEditorialStageExecutor,
    _load_transcript_evidence,
    _align_emphasized_phrases,
)
from subtitler.errors import SubtitlerError
from subtitler.media_analysis import MediaAnalysisResult


class HostedEditorialTests(unittest.TestCase):
    def test_editorial_and_subtitle_cleanup_models_are_independent(self) -> None:
        executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
        executor.config = {
            "cleanup": {"backend": "openai", "api_model": "user-cleanup"},
            "editorial": {
                "analysis_model": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "director_model": "gpt-5.6-terra",
                "director_reasoning_effort": "low",
                "subtitle_cleanup_model": "gpt-5.4-mini",
                "subtitle_cleanup_reasoning_effort": "medium",
            },
        }

        editorial = executor._editorial_model_config()
        director = executor._director_model_config()
        cleanup = executor._subtitle_cleanup_model_config()

        self.assertEqual(editorial["cleanup"]["api_model"], "gpt-5.6-luna")
        self.assertEqual(editorial["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(director["cleanup"]["api_model"], "gpt-5.6-terra")
        self.assertEqual(director["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(cleanup["cleanup"]["api_model"], "gpt-5.4-mini")
        self.assertEqual(cleanup["cleanup"]["reasoning_effort"], "medium")
        self.assertEqual(executor.config["cleanup"]["api_model"], "user-cleanup")

    def test_editorial_analysis_defaults_to_medium_reasoning(self) -> None:
        executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
        executor.config = {"editorial": {}}

        self.assertEqual(
            executor._editorial_model_config()["cleanup"]["reasoning_effort"],
            "medium",
        )

    def test_failed_director_retry_reuses_completed_global_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=root / "workspace",
                pipeline_script=root / "pipeline.py",
            )
            executor.config = {
                "cleanup": {},
                "editorial": {
                    "analysis_model": "gpt-5.6-luna",
                    "director_model": "gpt-5.6-terra",
                },
            }
            project = {
                "editorial_map": {"global_reconciliation": {"output": None}},
                "sources": [],
            }
            base = {"global_threads": [], "conflicts": [], "editorial_direction_summary": "Base"}
            global_refiner = Mock()
            global_refiner.complete_structured = Mock()
            director_refiner = Mock()
            director_refiner.complete_structured = Mock()
            with (
                patch.object(executor, "_build_editorial_refiner", return_value=global_refiner),
                patch.object(executor, "_build_director_refiner", return_value=director_refiner),
                patch("subtitler.editorial_hosted.reconcile_editorial_project", return_value=base) as reconcile,
                patch("subtitler.editorial_hosted.review_editorial_project", side_effect=RuntimeError("director failed")),
                self.assertRaises(RuntimeError) as raised,
            ):
                executor.finalize_project(project)
            failure_output = raised.exception.editorial_failure_output
            self.assertEqual(failure_output["base_reconciliation"], base)
            self.assertEqual(reconcile.call_count, 1)

            project["editorial_map"]["global_reconciliation"]["output"] = failure_output
            director_review = {"executive_direction": "Final"}
            with (
                patch.object(executor, "_build_editorial_refiner") as build_global,
                patch.object(executor, "_build_director_refiner", return_value=director_refiner),
                patch("subtitler.editorial_hosted.reconcile_editorial_project") as reconcile_again,
                patch("subtitler.editorial_hosted.review_editorial_project", return_value=director_review),
            ):
                result = executor.finalize_project(project)

            build_global.assert_not_called()
            reconcile_again.assert_not_called()
            self.assertEqual(result["director_review"], director_review)
            self.assertEqual(result["director_model"], "gpt-5.6-terra")

    def test_global_model_calls_report_progress_while_waiting(self) -> None:
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
                "editorial_map": {"global_reconciliation": {"output": None}},
                "sources": [],
            }
            base = {"global_threads": [], "conflicts": []}
            director_review = {"final_actions": [], "supporting_edits": [], "threads": []}
            refiner = Mock()

            def slow_result(value: dict[str, object]) -> dict[str, object]:
                time.sleep(0.035)
                return value

            output = io.StringIO()
            with (
                patch.object(executor, "_build_editorial_refiner", return_value=refiner),
                patch.object(executor, "_build_director_refiner", return_value=refiner),
                patch(
                    "subtitler.editorial_hosted.reconcile_editorial_project",
                    side_effect=lambda **_kwargs: slow_result(base),
                ),
                patch(
                    "subtitler.editorial_hosted.review_editorial_project",
                    side_effect=lambda **_kwargs: slow_result(director_review),
                ),
                patch("subtitler.editorial_hosted.EDITORIAL_PROGRESS_FIRST_UPDATE_SECONDS", 0.01),
                patch("subtitler.editorial_hosted.EDITORIAL_PROGRESS_UPDATE_INTERVAL_SECONDS", 0.02),
                redirect_stdout(output),
            ):
                executor.finalize_project(project)

            logs = output.getvalue()
            self.assertIn("Editorial synthesis: the hosted model is still processing", logs)
            self.assertIn("Editorial director: the hosted model is still processing", logs)

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
                patch("subtitler.editorial_hosted.OpenAIMediaAnalysisProvider"),
                patch("subtitler.editorial_hosted.analyze_media", return_value=analysis) as analyze,
                patch("subtitler.editorial_hosted.analyze_acoustic_emphasis", return_value=[]),
                patch("subtitler.editorial_hosted.analyze_temporal_bursts", return_value={"bursts": [], "cost_usd": 0.0}),
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
