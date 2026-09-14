import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aviutl_subtitle
from subtitler.api_usage import ApiUsageLedger
from subtitler.config import load_workflow_config
from subtitler.errors import SubtitlerError
from subtitler.media_export import SourceLayout
from subtitler.models import AlignedToken, ExoMarker, ExoSettings, Subtitle
from subtitler.review_exchange import ReviewStorage
from subtitler.silence_cut import build_cut_candidates
from subtitler.silence_review import request_review
from subtitler.subtitle_checkpoint import load_subtitle_checkpoint, save_subtitle_checkpoint
from subtitler.subtitle_stage import SubtitleStageOutcome
from subtitler.transcript_document import create_transcript_document, write_transcript_document
from subtitler.transcription_backend import BackendTranscriptResult, RawVadSpeechInterval
from subtitler.transcription_stage import TranscriptionStageOutcome


class ReviewResumeTests(unittest.TestCase):
    def test_cleanup_failure_resumes_transcription_and_preserves_failed_request_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, transcript_path, document = _transcript(root)
            config = load_workflow_config("hosted")
            config["audio"]["track"] = 0
            config["additional_settings"]["cut_silence_mode"] = "off"
            config["additional_settings"]["broll_mode"] = "off"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            argv = ["aviutl_subtitle.py", str(source), "--workflow", "hosted", "--config", str(config_path),
                    "--output", str(root / "out.exo"), "--sidecar-dir", str(root), "--no-glossary"]
            transcription = TranscriptionStageOutcome(document.backend, [], 6, document_path=transcript_path,
                                                       revision_id=document.revision_id)
            def fail_cleanup(_request, _aligned, _glossary, usage):
                usage.add(provider="test", model="test", operation="cleanup", cost_usd=.25)
                raise SubtitlerError("cleanup interrupted")
            with (patch("sys.argv", argv),
                  patch("subtitler.subtitle_workflow.run_transcription_stage", return_value=transcription),
                  patch("subtitler.subtitle_workflow.run_subtitle_stage", side_effect=fail_cleanup),
                  contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                self.assertEqual(aviutl_subtitle.main(), 1)
            # Subtitle and export choices are downstream of the completed transcript.
            config["subtitles"]["max_chars"] = 40
            config["cleanup"]["backend"] = "none"
            config["exo"]["font_size"] = 45
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = SubtitleStageOutcome([Subtitle(0, 1, "Recovered")], [], [])
            with (patch("sys.argv", argv),
                  patch("subtitler.subtitle_workflow.run_transcription_stage", side_effect=AssertionError("ASR repeated")),
                  patch("subtitler.subtitle_workflow.run_subtitle_stage", return_value=result),
                  patch("subtitler.subtitle_workflow.prepare_source_layout", return_value=SourceLayout(ExoSettings(), None)),
                  contextlib.redirect_stdout(io.StringIO()) as console):
                self.assertEqual(aviutl_subtitle.main(), 0)
            self.assertIn("Resuming completed transcription", console.getvalue())
            saved = json.loads((root / "out.pending.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["usage"][0]["cost_usd"], .25)
            self.assertEqual(saved["subtitles"]["subtitles"][0]["text"], "Recovered")

    def test_invalid_decisions_leave_the_same_review_pending(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / "review.json"
            storage = ReviewStorage(path, "revision")
            candidates = build_cut_candidates([RawVadSpeechInterval(0, 1), RawVadSpeechInterval(4, 5)])
            with patch("sys.stdin", io.StringIO()), self.assertRaises(SubtitlerError):
                request_review(candidates, "stdio-v1", storage)
            pending = json.loads(path.read_text(encoding="utf-8"))
            decision = {"candidateId": candidates[0].id, "decision": "reject_cut"}
            for decisions in ([], [decision, decision], [{**decision, "decision": []}], [{**decision, "candidateId": {}}]):
                response = {"type": "silence-review-result", "reviewId": pending["request"]["reviewId"], "decisions": decisions}
                with self.subTest(decisions=decisions), patch("sys.stdin", io.StringIO(json.dumps(response))), self.assertRaises(SubtitlerError):
                    request_review(candidates, "stdio-v1", storage)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), pending)

    def test_pending_and_accepted_reviews_survive_restart_and_reject_stale_decisions(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / "review.json"
            storage = ReviewStorage(path, "revision-1")
            candidates = build_cut_candidates([RawVadSpeechInterval(0, 1), RawVadSpeechInterval(4, 5)])
            with patch("sys.stdin", io.StringIO()), self.assertRaises(SubtitlerError):
                request_review(candidates, "stdio-v1", storage)
            pending = json.loads(path.read_text(encoding="utf-8"))
            response = {"type": "silence-review-result", "reviewId": pending["request"]["reviewId"],
                        "decisions": [{"candidateId": candidates[0].id, "decision": "reject_cut"}]}
            with patch("sys.stdin", io.StringIO(json.dumps(response))):
                result = request_review(candidates, "stdio-v1", storage)
            with patch("sys.stdin.readline", side_effect=AssertionError("must reuse accepted decisions")):
                self.assertEqual(request_review(candidates, "stdio-v1", storage), result)
            with patch("sys.stdin", io.StringIO(json.dumps(response))), self.assertRaises(SubtitlerError):
                request_review(candidates, "stdio-v1", ReviewStorage(path, "revision-2"))
            self.assertIsNone(json.loads(path.read_text(encoding="utf-8"))["response"])

    def test_checkpoint_preserves_completed_work_and_rejects_replaced_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, transcript_path, document = _transcript(root)
            result = SubtitleStageOutcome(
                [Subtitle(0, 1, "字幕", [AlignedToken("字幕", 0.1, 0.9)], chain_index=2, outline_color="ff0000")],
                [ExoMarker(0, 1, "Chapter", 1)], [ExoMarker(0, 1, "Check")],
            )
            usage = ApiUsageLedger()
            usage.add(provider="test", model="test", operation="cleanup", cost_usd=0.25)
            checkpoint = save_subtitle_checkpoint(root / "pending.json", signature="settings", transcript_path=transcript_path,
                                                  subtitles=result, usage=usage)
            def load(signature="settings"):
                return load_subtitle_checkpoint(checkpoint.path, signature=signature, source_path=source, audio_track=0)
            self.assertEqual(load().subtitles, result)
            self.assertEqual(load().usage[0].cost_usd, 0.25)
            self.assertIsNone(load("changed-settings"))
            # Public exports are mutable; the run dependency is a separate snapshot.
            transcript_path.unlink()
            self.assertEqual(load().transcript.revision_id, document.revision_id)
            write_transcript_document(transcript_path, document)
            checkpoint.finish()
            self.assertIsNotNone(load())
            checkpoint = save_subtitle_checkpoint(checkpoint.path, signature="settings", transcript_path=transcript_path,
                                                  subtitles=result, usage=usage)
            transcript_path = checkpoint.transcript_path
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            payload["revision_id"] = "replacement"
            transcript_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SubtitlerError, "replaced"):
                load()
            write_transcript_document(transcript_path, document)
            payload = json.loads(checkpoint.path.read_text(encoding="utf-8"))
            payload["subtitles"]["subtitles"][0]["tokens"][0]["start"] = -1
            checkpoint.path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SubtitlerError, "invalid source-timed"):
                load()

    def test_cli_restart_skips_transcription_and_cleanup_then_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, transcript_path, document = _transcript(root)
            config = load_workflow_config("hosted")
            config["additional_settings"]["cut_silence_mode"] = "review"
            config["audio"]["track"] = 0
            config["additional_settings"]["broll_mode"] = "off"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            argv = ["aviutl_subtitle.py", str(source), "--workflow", "hosted", "--config", str(config_path),
                    "--output", str(root / "out.exo"), "--sidecar-dir", str(root), "--no-glossary",
                    "--frontend-protocol", "stdio-v1"]
            transcription = TranscriptionStageOutcome(document.backend, [], 6, document_path=transcript_path,
                                                       revision_id=document.revision_id)
            subtitles = SubtitleStageOutcome([Subtitle(0, 1, "Saved cleanup")], [], [])
            with (
                patch("sys.argv", argv), patch("sys.stdin", io.StringIO()),
                patch("subtitler.subtitle_workflow.run_transcription_stage", return_value=transcription) as transcribe,
                patch("subtitler.subtitle_workflow.run_subtitle_stage", return_value=subtitles) as plan,
                patch("subtitler.subtitle_workflow.prepare_source_layout", return_value=SourceLayout(ExoSettings(), None)),
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(aviutl_subtitle.main(), 1)
                transcribe.assert_called_once()
                plan.assert_called_once()
            pending = json.loads((root / "out.pending.silence-review.json").read_text(encoding="utf-8"))
            response = {"type": "silence-review-result", "reviewId": pending["request"]["reviewId"],
                        "decisions": [{"candidateId": "silence-0001", "decision": "reject_cut"}]}
            with (
                patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(response))),
                patch("subtitler.subtitle_workflow.run_transcription_stage", side_effect=AssertionError("ASR repeated")),
                patch("subtitler.subtitle_workflow.run_subtitle_stage", side_effect=AssertionError("cleanup repeated")),
                patch("subtitler.subtitle_workflow.prepare_source_layout", return_value=SourceLayout(ExoSettings(), None)),
                contextlib.redirect_stdout(io.StringIO()) as console,
            ):
                self.assertEqual(aviutl_subtitle.main(), 0)
                self.assertIn("Resuming saved subtitle plan", console.getvalue())
            self.assertTrue((root / "out.exo").is_file())
            self.assertEqual(json.loads((root / "out.pending.json").read_text(encoding="utf-8"))["state"], "complete")
            # Completed runs also support export-only changes without model calls.
            config["exo"]["font_size"] = 54
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with (
                patch("sys.argv", argv), patch("sys.stdin.readline", side_effect=AssertionError("review repeated")),
                patch("subtitler.subtitle_workflow.run_transcription_stage", side_effect=AssertionError("ASR repeated")),
                patch("subtitler.subtitle_workflow.run_subtitle_stage", side_effect=AssertionError("cleanup repeated")),
                patch("subtitler.subtitle_workflow.prepare_source_layout", return_value=SourceLayout(ExoSettings(), None)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(aviutl_subtitle.main(), 0)



def _transcript(root):
    source = root / "source.wav"
    source.write_bytes(b"source media")
    document = create_transcript_document(
        source_path=source, audio_track=0, duration_sec=6, settings={},
        backend=BackendTranscriptResult("test", duration_sec=6, raw_vad_speech_intervals=[
            RawVadSpeechInterval(0, 1), RawVadSpeechInterval(4, 5),
        ]),
    )
    path = root / "transcript.json"
    write_transcript_document(path, document)
    return source, path, document
