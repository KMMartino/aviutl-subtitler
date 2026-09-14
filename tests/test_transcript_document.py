import json
import os
import tempfile
import unittest
from pathlib import Path

from subtitler.errors import SubtitlerError
from subtitler.transcript_document import create_transcript_document, load_transcript_document, write_transcript_document
from subtitler.transcription_backend import BackendTranscriptResult, SpeechRegion, TranscriptSegment, TranscriptToken


class TranscriptDocumentTests(unittest.TestCase):
    def test_round_trip_retains_alignment_groups_and_selected_speech_without_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media.wav"
            source.write_bytes(b"media")
            backend = BackendTranscriptResult(
                "test", language="ja", duration_sec=5,
                segments=[TranscriptSegment(0, "声。", 1, 2, tokens=[TranscriptToken("声。", 1.1, 1.8, "char")],
                                            metadata={"vad_group_index": 3})],
                speech_regions=[SpeechRegion(0, 1.128, 1.792), SpeechRegion(1, 4, 5, selected_for_transcription=False)],
            )
            document = create_transcript_document(
                source_path=source, audio_track=0, duration_sec=5, settings={"language": "ja"}, backend=backend,
            )
            path = root / "transcript.json"
            write_transcript_document(path, document)
            source.unlink()
            loaded = load_transcript_document(path)
            self.assertEqual(loaded.backend, backend)
            self.assertEqual(loaded.speech_activity_ms(), [(1128, 1792)])
            self.assertEqual(loaded.aligned_tokens()[0].start, 1.1)
            loaded.aligned_tokens()[0].text = "changed"
            self.assertEqual(loaded.aligned_tokens()[0].text, "声。")

    def test_reuse_rejects_wrong_track_changed_source_and_partial_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "media.wav"
            source.write_bytes(b"original")
            backend = BackendTranscriptResult("test")
            document = create_transcript_document(
                source_path=source, audio_track=1, duration_sec=1, settings={}, backend=backend,
            )
            document.require_reusable(source, 1)
            with self.assertRaisesRegex(SubtitlerError, "different audio track"):
                document.require_reusable(source, 0)
            backend.status = "partial"
            with self.assertRaisesRegex(SubtitlerError, "incomplete"):
                document.require_reusable(source, 1)
            backend.status = "ok"
            before = source.stat()
            source.write_bytes(b"modified")
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            with self.assertRaisesRegex(SubtitlerError, "does not match"):
                document.require_reusable(source, 1)

    def test_invalid_aligned_timing_is_rejected_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media.wav"
            source.write_bytes(b"media")
            document = create_transcript_document(
                source_path=source, audio_track=0, duration_sec=2, settings={},
                backend=BackendTranscriptResult("test", segments=[TranscriptSegment(
                    0, "Voice", 0, 1, tokens=[TranscriptToken("Voice", 0, 1)],
                )]),
            )
            path = root / "transcript.json"
            write_transcript_document(path, document)
            payload = json.loads(path.read_text(encoding="utf-8"))
            for value in (float("nan"), -1, "1", None):
                with self.subTest(value=value):
                    payload["backend"]["segments"][0]["tokens"][0]["end"] = value
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(SubtitlerError, "timestamp"):
                        load_transcript_document(path)
