import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.api_usage import ApiUsageLedger
from subtitler.run_artifacts import build_run_artifact_paths, format_elapsed, write_run_metadata
from subtitler.transcription_backend import BackendCapability, BackendDiagnostic, BackendTranscriptResult


class RunArtifactPathTests(unittest.TestCase):
    def test_builds_all_paths_from_output_stem_in_requested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            directory = root / "diagnostics"
            paths = build_run_artifact_paths(
                root / "input.mkv",
                root / "render.exo",
                enabled=True,
                directory=directory,
            )

            self.assertTrue(directory.is_dir())
            self.assertEqual(paths.base, directory / "render")
            self.assertEqual(paths.run_metadata, directory / "render.run.json")
            self.assertEqual(paths.cleanup_diff, directory / "render.cleanup_diff.txt")
            self.assertEqual(paths.chapter_markers, directory / "render.youtube_chapters.json")

    def test_disabled_sidecars_have_no_paths_and_create_no_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            directory = root / "unused"
            paths = build_run_artifact_paths(
                root / "input.mkv",
                root / "render.exo",
                enabled=False,
                directory=directory,
            )

            self.assertFalse(directory.exists())
            self.assertIsNone(paths.directory)
            self.assertIsNone(paths.base)
            self.assertIsNone(paths.run_metadata)


class RunMetadataTests(unittest.TestCase):
    def test_metadata_records_explicit_argv_and_key_presence_without_secret_values(self) -> None:
        args = argparse.Namespace(input="input.mkv", output="output.exo", workflow="hosted")
        backend = BackendTranscriptResult(
            backend_name="test",
            model_name="model",
            capabilities=BackendCapability(provides_vad=True),
            diagnostics=[BackendDiagnostic("warning", "partial item", code="partial")],
            metadata={"chunks": 2},
        )
        usage = ApiUsageLedger()
        usage.add(
            provider="openai",
            model="model",
            operation="transcription",
            input_tokens=4,
            output_tokens=2,
            cost_usd=0.25,
        )

        with tempfile.TemporaryDirectory() as temp_name, mock.patch.dict(
            "os.environ", {"OPENAI_API_KEY": "do-not-write-this"}, clear=True
        ):
            root = Path(temp_name)
            path = root / "run.json"
            write_run_metadata(
                path,
                args,
                {"backend": {"name": "test"}},
                root / ".env",
                ["OPENAI_API_KEY"],
                backend,
                usage,
                root / "usage.csv",
                65.2,
                argv=["input.mkv", "--workflow", "hosted"],
            )
            text = path.read_text(encoding="utf-8")
            metadata = json.loads(text)

        self.assertNotIn("do-not-write-this", text)
        self.assertEqual(metadata["argv"], ["input.mkv", "--workflow", "hosted"])
        self.assertEqual(metadata["elapsed_run_display"], "1m 5s")
        self.assertEqual(metadata["actual_api_total_tokens"], 6)
        self.assertTrue(metadata["api_keys_present"]["OPENAI_API_KEY"])
        self.assertFalse(metadata["api_keys_present"]["GEMINI_API_KEY"])
        self.assertEqual(metadata["backend"]["diagnostics"][0]["code"], "partial")

    def test_elapsed_formatting_boundaries(self) -> None:
        self.assertEqual(format_elapsed(-1), "0s")
        self.assertEqual(format_elapsed(59.6), "1m 0s")
        self.assertEqual(format_elapsed(3661), "1h 1m 1s")


if __name__ == "__main__":
    unittest.main()
