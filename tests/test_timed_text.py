import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from subtitler.errors import SubtitlerError
from subtitler.timed_text import TimedTextDocument, TimedTextSpan, load_timed_text, write_timed_text


class TimedTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = TimedTextDocument(
            "revision", "source.mp4", 1, True, True,
            (TimedTextSpan(1.25, 2.5, "1. 日本語\nTwo lines"),),
        )

    def test_round_trip_preserves_text_timing_and_source_without_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.json"
            write_timed_text(path, self.document)
            self.assertEqual(load_timed_text(path, require_complete_raw=True), self.document)

    def test_editorial_rejects_partial_or_cleaned_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.json"
            for document in (replace(self.document, complete=False), replace(self.document, raw_transcript=False)):
                with self.subTest(document=document):
                    write_timed_text(path, document)
                    self.assertEqual(load_timed_text(path), document)
                    with self.assertRaisesRegex(SubtitlerError, "complete raw transcript"):
                        load_timed_text(path, require_complete_raw=True)

    def test_corrupt_contracts_are_rejected_before_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.json"
            write_timed_text(path, self.document)
            original = json.loads(path.read_text(encoding="utf-8"))
            for changes in (
                {"schema_version": True}, {"schema_version": 2}, {"time_base": "output_frames"},
                {"complete": "true"}, {"audio_track": -1}, {"spans": [{"text": "Missing timing"}]},
                {"spans": [{"start_sec": float("nan"), "end_sec": 2, "text": "NaN"}]},
                {"spans": [{"start_sec": 3, "end_sec": 2, "text": "Reversed"}]},
            ):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps(original | changes), encoding="utf-8")
                    with self.assertRaises(SubtitlerError):
                        load_timed_text(path)

    def test_failed_commit_preserves_previous_document_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.json"
            write_timed_text(path, self.document)
            with patch.object(Path, "replace", side_effect=OSError("disk error")):
                with self.assertRaisesRegex(OSError, "disk error"):
                    write_timed_text(path, replace(self.document, revision_id="next"))
            self.assertEqual(load_timed_text(path), self.document)
            self.assertEqual(list(Path(directory).iterdir()), [path])
