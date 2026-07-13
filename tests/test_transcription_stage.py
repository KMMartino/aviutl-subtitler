import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.api_usage import ApiUsageLedger
from subtitler.errors import SubtitlerError
from subtitler.glossary import GlossaryEntry
from subtitler.profiling import PipelineProfiler
from subtitler.run_artifacts import build_run_artifact_paths
from subtitler.run_context import CliArguments, RunContext
from subtitler.transcription_backend import (
    BackendDiagnostic,
    BackendTranscriptResult,
    TranscriptSegment,
    TranscriptToken,
)
from subtitler.transcription_stage import run_transcription_stage


def _context(root: Path, *, diagnostics: bool = True) -> RunContext:
    input_path = root / "input.mkv"
    input_path.touch()
    output_path = root / "output.exo"
    args = CliArguments(
        input=str(input_path),
        workflow="local",
        output=str(output_path),
        config=None,
        env_file=".env",
        profile=diagnostics,
        audio_track=None,
        sidecar_dir=str(root / "sidecars"),
        no_sidecars=False,
        glossary=None,
        no_glossary=False,
    )
    return RunContext(
        args=args,
        input_path=input_path,
        output_path=output_path,
        config_path=root / "config.json",
        config={
            "audio": {"track": 2},
            "backend": {"name": "existing-pipeline", "language": "ja"},
        },
        env_path=root / ".env",
        loaded_env_keys=[],
        sidecars_enabled=True,
        diagnostics_enabled=diagnostics,
        artifacts=build_run_artifact_paths(
            input_path,
            output_path,
            enabled=True,
            directory=root / "sidecars",
        ),
    )


class TranscriptionStageTests(unittest.TestCase):
    def test_runs_audio_glossary_backend_normalization_and_aligned_sidecar(self) -> None:
        result = BackendTranscriptResult(
            backend_name="test",
            segments=[
                TranscriptSegment(
                    index=1,
                    text="字幕です",
                    start=0.0,
                    end=1.0,
                    tokens=[TranscriptToken("字幕です", 0.0, 1.0)],
                )
            ],
        )
        backend = mock.Mock()
        backend.transcribe.return_value = result

        def extract_side_effect(*args, **kwargs):
            kwargs["progress_callback"](10.0)

        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(root)
            output = io.StringIO()
            with mock.patch("subtitler.transcription_stage.get_media_duration", return_value=0.0), mock.patch(
                "subtitler.transcription_stage.extract_audio", side_effect=extract_side_effect
            ) as extract, mock.patch(
                "subtitler.transcription_stage.load_mono_16k_wav", return_value=([0, 1, 2, 3], 2)
            ), mock.patch(
                "subtitler.transcription_stage.find_glossary", return_value=root / "glossary.txt"
            ) as find_glossary, mock.patch(
                "subtitler.transcription_stage.load_glossary", return_value=[GlossaryEntry("用語")]
            ), mock.patch(
                "subtitler.transcription_stage.build_backend", return_value=backend
            ), contextlib.redirect_stdout(output):
                outcome = run_transcription_stage(
                    context,
                    root / "temp",
                    ApiUsageLedger(),
                    PipelineProfiler(False, None),
                    project_dir=root / "project",
                )

            request = backend.transcribe.call_args.args[0]
            aligned_text = context.artifacts.aligned_text.read_text(encoding="utf-8")

        self.assertFalse(outcome.cost_estimate_only)
        self.assertEqual(outcome.duration_sec, 2.0)
        self.assertEqual(outcome.glossary, [GlossaryEntry("用語")])
        self.assertEqual([item.text for item in outcome.aligned], ["字幕です"])
        self.assertEqual(request.duration_sec, 2.0)
        self.assertEqual(request.sample_rate, 2)
        self.assertEqual(request.workflow, "local")
        self.assertEqual(request.profile_enabled, True)
        self.assertEqual(request.sidecar_base, context.artifacts.base)
        self.assertEqual(request.metadata["samples"], [0, 1, 2, 3])
        self.assertTrue(callable(request.metadata["stage_progress_reporter"]))
        extract.assert_called_once_with(
            context.input_path,
            root / "temp" / "input_16k_mono.wav",
            2,
            duration=0.0,
            progress_callback=mock.ANY,
        )
        find_glossary.assert_called_once_with(
            input_path=context.input_path,
            explicit=None,
            disabled=False,
            project_dir=root / "project",
        )
        self.assertIn("Extracting mono 16 kHz audio...", output.getvalue())
        self.assertIn("Audio extraction progress: 10%", output.getvalue())
        self.assertIn("Loaded glossary entries: 1", output.getvalue())
        self.assertIn("字幕です", aligned_text)

    def test_cost_estimate_outcome_skips_status_normalization_and_sidecar(self) -> None:
        result = BackendTranscriptResult(
            backend_name="test",
            status="partial",
            diagnostics=[BackendDiagnostic("info", "estimate", code="cost_estimate_only")],
        )
        backend = mock.Mock()
        backend.transcribe.return_value = result
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(root)
            with mock.patch("subtitler.transcription_stage.get_media_duration", return_value=10.0), mock.patch(
                "subtitler.transcription_stage.extract_audio"
            ), mock.patch(
                "subtitler.transcription_stage.load_mono_16k_wav", return_value=([0], 16_000)
            ), mock.patch(
                "subtitler.transcription_stage.load_glossary", return_value=[]
            ), mock.patch(
                "subtitler.transcription_stage.build_backend", return_value=backend
            ), mock.patch(
                "subtitler.transcription_stage.handle_backend_result_status"
            ) as handle_status, mock.patch(
                "subtitler.transcription_stage.backend_result_to_aligned_chunks"
            ) as normalize:
                outcome = run_transcription_stage(
                    context,
                    root / "temp",
                    ApiUsageLedger(),
                    PipelineProfiler(False, None),
                    project_dir=root,
                )

            self.assertTrue(outcome.cost_estimate_only)
            self.assertEqual(outcome.aligned, [])
            self.assertFalse(context.artifacts.aligned_text.exists())
        handle_status.assert_not_called()
        normalize.assert_not_called()

    def test_failed_backend_result_preserves_actionable_error_and_writes_no_aligned_sidecar(self) -> None:
        backend = mock.Mock()
        backend.transcribe.return_value = BackendTranscriptResult(backend_name="test", status="failed")
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(root)
            with mock.patch("subtitler.transcription_stage.get_media_duration", return_value=1.0), mock.patch(
                "subtitler.transcription_stage.extract_audio"
            ), mock.patch(
                "subtitler.transcription_stage.load_mono_16k_wav", return_value=([0], 16_000)
            ), mock.patch(
                "subtitler.transcription_stage.load_glossary", return_value=[]
            ), mock.patch("subtitler.transcription_stage.build_backend", return_value=backend):
                with self.assertRaisesRegex(
                    SubtitlerError, "selected speech produced no usable transcript segments"
                ):
                    run_transcription_stage(
                        context,
                        root / "temp",
                        ApiUsageLedger(),
                        PipelineProfiler(False, None),
                        project_dir=root,
                    )
            self.assertFalse(context.artifacts.aligned_text.exists())


if __name__ == "__main__":
    unittest.main()
