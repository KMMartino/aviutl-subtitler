import copy
import json
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitler.config import load_workflow_config
from subtitler.errors import SubtitlerError
from subtitler.models import AlignedChunk, AudioChunk
from subtitler.timed_text import load_timed_text
from subtitler.transcription_backend import BackendDiagnostic, BackendTranscriptResult
from subtitler.transcript_document import create_transcript_document, write_transcript_document
from subtitler.editorial_hosted import HostedEditorialExecutorOptions, HostedEditorialStageExecutor
from subtitler.editorial_project import EditorialProjectOptions, EditorialSourceInput, create_editorial_project, write_editorial_checkpoint
from subtitler.editorial_project_cli import main as editorial_main
from subtitler.transcription_stage import TranscriptionStageOutcome
from subtitler.transcript_workflow import run_transcript_workflow


class TranscriptWorkflowTests(unittest.TestCase):
    def test_editorial_executor_reuses_a_supplied_transcript_without_asr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            source.write_bytes(b'source identity')
            artifact = root / 'saved.json'
            document = create_transcript_document(source_path=source, audio_track=0, duration_sec=3,
                settings={}, backend=BackendTranscriptResult('test', status='ok', duration_sec=3))
            write_transcript_document(artifact, document)
            config = root / 'config.json'
            config.write_text(json.dumps(load_workflow_config('hosted-long-stream')), encoding='utf-8')
            options = HostedEditorialExecutorOptions(config, root / '.env', root / 'work',
                audio_track=0, transcript_artifacts=(artifact,))
            executor = HostedEditorialStageExecutor(options)
            with patch('subtitler.transcription_stage.build_backend', side_effect=AssertionError('unexpected ASR')):
                result = executor._transcribe({'source_id': 'source-1', 'audio_path': str(source)})
            self.assertEqual(result['api_cost_usd'], 0)
            reused = load_timed_text(Path(result['document_path']), require_complete_raw=True)
            self.assertEqual(reused.input_revision_id, document.revision_id)
            project = create_editorial_project([EditorialSourceInput(source, 3000)],
                EditorialProjectOptions('test', 'test reuse', 1000, 2000))
            project['sources'][0]['stages']['transcription'].update(
                status='complete', output={'transcript_path': str(artifact)})
            checkpoint = root / 'project.json'
            write_editorial_checkpoint(checkpoint, project)
            replacement = root / 'replacement.json'
            write_transcript_document(replacement, replace(document, revision_id='different-revision'))
            with patch('subtitler.editorial_project_cli.run_editorial_project') as run:
                self.assertEqual(editorial_main(['run', '--checkpoint', str(checkpoint),
                    '--config', str(config), '--audio-track', '0', '--transcript-artifact', str(replacement)]), 1)
                run.assert_not_called()
            source.write_bytes(b'changed source identity')
            with self.assertRaises(SubtitlerError):
                HostedEditorialStageExecutor(options)

    def test_raw_transcript_composition_accepts_local_or_hosted_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            source.write_bytes(b"source")
            saved = root / "saved.json"
            write_transcript_document(saved, create_transcript_document(
                source_path=source, audio_track=0, duration_sec=3, settings={},
                backend=BackendTranscriptResult("test", status="ok", duration_sec=3),
            ))
            for workflow in ("local", "hosted", "hosted-long-stream"):
                with self.subTest(workflow=workflow), patch("subtitler.transcription_stage.build_backend",
                        side_effect=AssertionError("unexpected transcription")):
                    result = run_transcript_workflow(source_path=source, config=load_workflow_config(workflow),
                        workspace=root / workflow, audio_track=0, glossary=[], reuse_document=saved, workflow=workflow)
                    self.assertTrue(result.document.complete)
                    self.assertEqual(result.api_cost_usd, 0)

    def test_raw_document_uses_selected_source_track_and_preserves_config(self) -> None:
        config = load_workflow_config("hosted-long-stream")
        original = copy.deepcopy(config)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def transcribe(request, temporary, usage, profiler):
                self.assertEqual(request.input_path, root / "facecam.mp4")
                self.assertEqual(request.config["audio"]["track"], 0)
                self.assertIsNone(request.on_speech_activity)
                usage.add(provider="test", model="test", operation="transcribe", cost_usd=0.25)
                return TranscriptionStageOutcome(
                    BackendTranscriptResult("test"),
                    [AlignedChunk(AudioChunk(0, 1.25, 2.5, None), "そのまま。", [], fallback=True)], 3,
                )

            with patch("subtitler.transcript_workflow.run_transcription_stage", side_effect=transcribe), patch(
                "subtitler.subtitle_stage.build_refiner", side_effect=AssertionError("raw text must not use cleanup")
            ):
                result = run_transcript_workflow(
                    source_path=root / "facecam.mp4", config=config, workspace=root / "work",
                    audio_track=0, glossary=[],
                )
            document = load_timed_text(result.document_path, require_complete_raw=True)
            self.assertEqual(document.source_path, str((root / "facecam.mp4").resolve()))
            self.assertEqual(document.audio_track, 0)
            self.assertEqual([span.text for span in document.spans], ["そのまま。"])
            self.assertEqual(result.api_cost_usd, 0.25)
            self.assertTrue(result.api_usage_path.is_file())
            self.assertFalse(list(root.rglob("*.exo")))
        self.assertEqual(config, original)

    def test_incomplete_or_estimate_only_transcription_never_reaches_planning(self) -> None:
        config = load_workflow_config("hosted-long-stream")
        cases = [
            (BackendTranscriptResult("test", status="partial"), False),
            (BackendTranscriptResult("test", diagnostics=[
                BackendDiagnostic("error", "unresolved group", code="transcription_failed", region_index=3),
            ]), False),
            (BackendTranscriptResult("test", status="partial"), True),
        ]
        for backend, estimate_only in cases:
            with self.subTest(status=backend.status, estimate_only=estimate_only), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)

                def transcribe(request, temporary, usage, profiler):
                    usage.add(provider="test", model="test", operation="transcribe", cost_usd=0.25)
                    return TranscriptionStageOutcome(backend, [], 3, estimate_only)

                with patch("subtitler.transcript_workflow.run_transcription_stage", side_effect=transcribe), patch(
                    "subtitler.transcript_workflow.run_subtitle_stage", side_effect=AssertionError("must not plan")
                ), self.assertRaises(SubtitlerError) as failure:
                    run_transcript_workflow(
                        source_path=root / "source.mp4", config=config, workspace=root / "work",
                        audio_track=0, glossary=[],
                    )
                self.assertEqual(failure.exception.editorial_failure_output["api_cost_usd"], 0.25)
                self.assertTrue((root / "work/transcript.api_usage.csv").is_file())
                self.assertFalse((root / "work/transcript.subtitles.json").exists())
