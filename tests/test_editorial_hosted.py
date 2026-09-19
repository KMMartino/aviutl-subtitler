from subtitler.speech_editing import align_selected_phrases, clean_selected_subtitles, tighten_transcript_to_speech
import io
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.editorial_analysis import EDITORIAL_PROMPT_VERSION, TranscriptEvidence, load_transcript_evidence
from subtitler.editorial_hosted import (
    HostedEditorialExecutorOptions,
    HostedEditorialStageExecutor,
    _analyze_editorial_visual_windows,
    _load_semantic_progress,
)
from subtitler.editorial_project import EDITORIAL_STAGE_VERSIONS
from subtitler.errors import SubtitlerError
from subtitler.models import AlignedToken
from subtitler.timed_text import TimedTextDocument, TimedTextSpan, write_timed_text
from subtitler.transcript_workflow import TranscriptWorkflowResult
from subtitler.media_analysis import MediaAnalysisResponseError, MediaAnalysisResult


class HostedEditorialTests(unittest.TestCase):
    def test_paired_sources_use_their_own_first_audio_tracks(self) -> None:
        from types import SimpleNamespace
        from subtitler.editorial_hosted import HostedEditorialStageExecutor
        executor = object.__new__(HostedEditorialStageExecutor)
        executor.options = SimpleNamespace(audio_track=1)
        executor.config = {"editorial": {"game_audio_track": 2}}
        sources = [{"visual_path": "game.mp4", "audio_path": "face.mp4", "media_mode": "paired"},
                   {"visual_path": "single.mkv", "audio_path": "single.mkv", "media_mode": "single"}]
        with patch("subtitler.editorial_hosted.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout='{"streams":[{},{},{}]}')):
            executor.prepare_project({"sources": sources})
        self.assertEqual([(s["audio_track"], s["game_audio_track"]) for s in sources], [(0, 0), (1, 2)])

    def test_semantic_progress_rejects_changed_language_or_transcript_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            saved = _load_semantic_progress(path, source_id="source", source_duration_ms=60000, evidence_identity="ja-transcript-1")
            saved["completed_windows"] = [{"base_window_index": 0}]
            path.write_text(json.dumps(saved), encoding="utf-8")
            self.assertEqual(_load_semantic_progress(path, source_id="source", source_duration_ms=60000, evidence_identity="ja-transcript-1")["completed_windows"], saved["completed_windows"])
            for identity in ("en-transcript-1", "ja-transcript-2"):
                self.assertEqual(_load_semantic_progress(path, source_id="source", source_duration_ms=60000, evidence_identity=identity)["completed_windows"], [])

    def test_editorial_transcript_edges_are_tightened_to_fine_vad(self) -> None:
        transcript = [TranscriptEvidence(190_024, 195_144, "いや、お前も悪いやつだろ。")]

        tightened = tighten_transcript_to_speech(
            transcript,
            [(187_704, 189_224), (191_128, 193_192)],
        )

        self.assertEqual(
            [(item.start_ms, item.end_ms, item.text) for item in tightened],
            [(191_128, 193_192, "いや、お前も悪いやつだろ。")],
        )

    def test_editorial_transcript_inside_continuous_vad_keeps_its_alignment(self) -> None:
        transcript = [TranscriptEvidence(10_000, 12_000, "continuous speech")]

        tightened = tighten_transcript_to_speech(
            transcript,
            [(9_000, 13_000)],
        )

        self.assertEqual(tightened, transcript)

    def test_cached_late_punctuation_is_attached_without_extending_utterance(self) -> None:
        transcript = [
            TranscriptEvidence(7_060_600, 7_061_384, "ナイス"),
            TranscriptEvidence(7_064_618, 7_064_698, "。"),
        ]

        tightened = tighten_transcript_to_speech(
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

        tightened = tighten_transcript_to_speech(
            transcript,
            [(1_000, 2_000), (7_000, 8_000)],
        )

        self.assertEqual(tightened, transcript)

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
            _analyze_editorial_visual_windows(**{**arguments, "output_locale": "en"})
            self.assertEqual(analyze.call_count, 4)
            _analyze_editorial_visual_windows(**{**arguments, "output_locale": "en", "editorial_context": "changed intent"})

        self.assertEqual(analyze.call_count, 6)
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
        ) as analyze, io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as console, patch("sys.stdout", console):
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

    def test_operation_revisions_isolate_partial_work_and_restore_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            original = HostedEditorialExecutorOptions(root / "config.json", root / ".env", root / "work")
            executor.options = original
            with executor.operation_scope("first"):
                saved = executor.options.workspace / "partial.json"
                saved.write_text("completed window", encoding="utf-8")
            with executor.operation_scope("second"):
                self.assertFalse((executor.options.workspace / "partial.json").exists())
            with self.assertRaises(RuntimeError), executor.operation_scope("first"):
                self.assertEqual((executor.options.workspace / "partial.json").read_text(encoding="utf-8"), "completed window")
                raise RuntimeError("interrupted")
            self.assertIs(executor.options, original)

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

        editorial = executor._model_config("analysis")
        director = executor._model_config("director")
        cleanup = executor._model_config("subtitle_cleanup")

        self.assertEqual(editorial["cleanup"]["api_model"], "gpt-5.6-luna")
        self.assertEqual(editorial["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(director["cleanup"]["api_model"], "gpt-5.6-terra")
        self.assertEqual(director["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(cleanup["cleanup"]["api_model"], "gpt-5.6-luna")
        self.assertEqual(cleanup["cleanup"]["reasoning_effort"], "low")
        self.assertEqual(executor.config["cleanup"]["api_model"], "user-cleanup")

    def test_cutting_assistant_selects_aligns_and_cleans_sparse_subtitles(self) -> None:
        from subtitler.transcript_document import create_transcript_document, write_transcript_document
        from subtitler.transcription_backend import BackendTranscriptResult

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=root / "workspace",
            )
            executor.config = {"cleanup": {}, "editorial": {"recommendations_enabled": True}}
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
                    "source_id": "source-1", "duration_ms": 1000,
                    "stages": {"transcription": {"output": {
                        "transcript_path": None
                    }}},
                }],
            }
            source = root / "source.mp4"
            source.write_bytes(b"source")
            transcript = root / "transcript.json"
            write_transcript_document(transcript, create_transcript_document(
                source_path=source, audio_track=0, duration_sec=1, settings={},
                backend=BackendTranscriptResult("test", status="ok", duration_sec=1),
            ))
            project["sources"][0]["stages"]["transcription"]["output"]["transcript_path"] = str(transcript)
            assessor = Mock()
            def assess(**kwargs):
                properties = kwargs['schema']['properties']['assessments']['items']['properties']
                return {'assessments': [{'target_id': target, 'suggested_treatment': 'inspect',
                    'observed_content': 'Unassigned opening', 'potential_contribution': 'Possible setup',
                    'reason_and_tradeoff': 'Inspect this opening before shortening it.',
                    'evidence_limitation': 'No visual observations supplied.', 'evidence_ids': [], 'related_ids': []}
                    for target in properties['target_id']['enum']]}
            assessor.inspect.side_effect = assess
            planner = Mock()
            cleaner = Mock()
            cleaner.refine.return_value = ["Cleaned line"]
            with (
                patch('subtitler.editorial_recommendations.HostedInspectionProvider', return_value=assessor),
                patch.object(executor, "_build_editorial_refiner", return_value=planner),
                patch.object(executor, "_build_subtitle_cleanup_refiner", return_value=cleaner) as cleanup,
                patch("subtitler.editorial_hosted.select_editorial_subtitles", return_value=[{
                    "source_id": "source-1", "start_ms": 100, "end_ms": 500,
                    "source_text": "Raw line", "reason": "Reaction",
                    "emphasis_energy": 0.5, "confidence": 0.9,
                }]),
                patch("subtitler.editorial_hosted.align_selected_phrases", return_value=[{
                    "source_id": "source-1", "start_ms": 120, "end_ms": 480,
                    "source_text": "Raw line", "text": "Raw line",
                    "timing_verified": True,
                }]),
            ):
                result = executor.plan_actions(project)

        from subtitler.editorial_recommendation_view import recommendation_html
        assessor.inspect.assert_called_once()
        self.assertEqual(result['gap_edge_mode'], 'acoustic')
        self.assertEqual(len(result['editor_recommendations']['assessments']), 1)
        rendered = recommendation_html(result['editor_recommendations'])
        self.assertIn('Inspect this opening before shortening it.', rendered)
        self.assertNotIn('No model assessment requested', rendered)
        self.assertEqual(result['confirmed_cuts'], [])
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
            )
            executor.config = {"cleanup": {}, "editorial": {"recommendations_enabled": False}}
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

    def test_global_overview_does_not_call_a_hosted_director(self) -> None:
        executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
        with patch.object(executor, '_build_director_refiner') as director:
            result = executor.finalize_project({'sources': []})
        director.assert_not_called()
        self.assertEqual(result['api_cost_usd'], 0)
        self.assertEqual(result['narration_briefs'], [])

    def test_emphasized_phrase_uses_verified_token_timing(self) -> None:
        result = align_selected_phrases(
            [{"id": "e", "source_text": "I'm cooked", "start_ms": 900, "end_ms": 2000}],
            [AlignedToken("I'm", 1, 1.2, "word"), AlignedToken("cooked", 1.2, 1.5, "word")],
        )
        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (1000, 1500))
        self.assertTrue(result[0]["timing_verified"])

    def test_long_selected_phrase_becomes_short_token_timed_display_beats(self) -> None:
        words = ["This", "is", "a", "surprisingly", "important", "discovery,", "and", "now", "we", "run."]
        source_text = " ".join(words)
        result = align_selected_phrases(
            [{"id": "phrase", "source_text": source_text, "start_ms": 900, "end_ms": 4000}],
            [AlignedToken(word, round(1 + index * 0.2, 1), round(1.2 + index * 0.2, 1), "word")
             for index, word in enumerate(words)],
        )
        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(item["text"]) <= 40 for item in result))
        self.assertEqual("".join("".join(item["text"].split()) for item in result), "".join(source_text.split()))
        self.assertEqual([item["display_segment_index"] for item in result], list(range(1, len(result) + 1)))
        self.assertTrue(all(left["end_ms"] <= right["start_ms"] for left, right in zip(result, result[1:])))

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

        result = clean_selected_subtitles([phrase], refiner)

        refiner.refine.assert_called_once_with(["raw phrase"])
        self.assertEqual(result[0]["text"], "Cleaned phrase。")
        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (1_000, 1_500))
        self.assertEqual(result[0]["source_text"], "raw phrase")
        self.assertTrue(result[0]["cleanup_applied"])

    def test_emphasized_phrase_clamps_silence_stretched_boundary_tokens(self) -> None:
        result = align_selected_phrases(
            [{"id": "e", "source_text": "AB!", "start_ms": 900, "end_ms": 6000}],
            [AlignedToken("A", 1, 2.5, "char"), AlignedToken("B", 2.5, 5, "char"), AlignedToken("!", 5, 5, "char")],
        )
        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (1750, 3250))

    def test_emphasized_phrase_rejects_internal_silence_stretch(self) -> None:
        result = align_selected_phrases(
            [{"id": "e", "source_text": "ABC", "start_ms": 900, "end_ms": 5000}],
            [AlignedToken("A", 1, 1.2, "char"), AlignedToken("B", 1.2, 4, "char"), AlignedToken("C", 4, 4.2, "char")],
        )
        self.assertEqual(result, [])

    def test_emphasized_phrase_deduplicates_punctuation_variants(self) -> None:
        result = align_selected_phrases(
            [
                {"id": "short", "source_text": "Good news", "start_ms": 900, "end_ms": 2000, "confidence": 0.9},
                {"id": "punctuated", "source_text": "Good news!", "start_ms": 900, "end_ms": 2000, "confidence": 0.9},
            ],
            [AlignedToken("Good", 1, 1.2, "word"), AlignedToken("news", 1.2, 1.5, "word"), AlignedToken("!", 1.5, 1.5, "char")],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "punctuated")

    def test_loads_raw_document_as_semantic_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document_path = Path(directory) / "transcript.json"
            write_timed_text(document_path, TimedTextDocument(
                "revision", "source.mp4", 0, True, True,
                (TimedTextSpan(1.25, 2.5, "First observation"), TimedTextSpan(3, 4.125, "Second observation")),
            ))
            evidence = load_transcript_evidence(document_path)
            self.assertEqual(
                [(item.start_ms, item.end_ms, item.text) for item in evidence],
                [(1250, 2500, "First observation"), (3000, 4125, "Second observation")],
            )

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
            patch("subtitler.source_inspection.get_media_duration", side_effect=[60.1, 60.0]),
            patch("subtitler.source_inspection._probe_frame_rate", return_value=60.0),
        ):
            result = executor._probe(source)
        self.assertEqual(result["audio_path"], "run-facecam.mp4")
        self.assertEqual(result["visual_path"], "run-gameplay.mp4")

        with (
            patch("subtitler.source_inspection.get_media_duration", side_effect=[60.2, 60.0]),
            patch("subtitler.source_inspection._probe_frame_rate", return_value=60.0),
            self.assertRaisesRegex(SubtitlerError, "more than 10 gameplay frames"),
        ):
            executor._probe(source)

    def test_paired_stages_transcribe_facecam_and_analyze_gameplay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            source_workspace = workspace / "source-1"
            source_workspace.mkdir(parents=True)
            document_path = source_workspace / "transcript.subtitles.json"
            document = TimedTextDocument("revision", str(root / "run-facecam.mp4"), 1, True, True,
                                         (TimedTextSpan(1, 2, "Voice line"),))
            write_timed_text(document_path, document)
            executor = HostedEditorialStageExecutor.__new__(HostedEditorialStageExecutor)
            executor.options = HostedEditorialExecutorOptions(
                config_path=root / "config.json",
                env_file=root / ".env",
                workspace=workspace,
            )
            executor.config = {"editorial": {"visual_detail": "simple"}}
            executor.transcript_artifacts = {}
            source = {
                "source_id": "source-1",
                "original_name": "run-gameplay.mp4",
                "audio_path": str(root / "run-facecam.mp4"),
                "visual_path": str(root / "run-gameplay.mp4"),
            }
            outcome = TranscriptWorkflowResult(
                document, document_path, source_workspace / "transcript.json",
                source_workspace / "usage.csv", 0.12,
            )
            with patch("subtitler.editorial_hosted.run_transcript_workflow", return_value=outcome) as transcribe:
                result = executor._transcribe(source)
            self.assertEqual(transcribe.call_args.kwargs["source_path"], Path(source["audio_path"]))
            self.assertEqual(result["document_revision"], "revision")
            self.assertEqual(result["api_cost_usd"], 0.12)
            with patch("subtitler.editorial_hosted.run_transcript_workflow", return_value=outcome) as transcribe:
                executor._transcribe({**source, "speech_source": "gameplay"})
            self.assertEqual(transcribe.call_args.kwargs["source_path"], Path(source["visual_path"]))


            analysis = MediaAnalysisResult("Gameplay", [], [], "openai", "model", "v1", 1, 1, 1, 0.01)
            refiner = Mock()
            refiner.complete_structured.return_value = json.dumps({})
            with (
                patch("subtitler.editorial_hosted.OpenAIEditorialVisualProvider"),
                patch("subtitler.editorial_hosted.analyze_media", return_value=analysis) as analyze,
                patch("subtitler.editorial_hosted.analyze_acoustic_emphasis", return_value=[]),
                patch("subtitler.editorial_hosted.load_game_profile", return_value={
                    "reference_context": {"status": "complete", "page_title": "Different game", "summary": "Wrong context"}
                }),
                patch("subtitler.editorial_hosted.lookup_game_wiki", return_value={"status": "unavailable"}) as lookup,
                patch("subtitler.editorial_hosted.build_refiner", return_value=refiner),
            ):
                executor._analyze_visuals(
                    source,
                    {"title_or_game": "Test game", "objective": "Finish the run"},
                    {"source_probe": {"duration_ms": 60_000}},
                )
            self.assertEqual(analyze.call_args.kwargs["media_path"], Path(source["visual_path"]))
            self.assertEqual(analyze.call_args.kwargs["sampling_scale"], 1.5)
            lookup.assert_called_once_with("Test game")
            self.assertNotIn("Wrong context", str(analyze.call_args))



if __name__ == "__main__":
    unittest.main()
