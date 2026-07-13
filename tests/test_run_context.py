import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.errors import SubtitlerError
from subtitler.run_context import CliArguments, RunContext, prepare_run_context


def _arguments(**overrides) -> CliArguments:
    values = {
        "input": "input.mkv",
        "workflow": "local",
        "output": None,
        "config": None,
        "env_file": ".env",
        "profile": False,
        "audio_track": None,
        "sidecar_dir": None,
        "no_sidecars": False,
        "glossary": None,
        "no_glossary": False,
    }
    values.update(overrides)
    return CliArguments(**values)


def _config() -> dict:
    return {
        "audio": {"track": 0},
        "diagnostics": {"profile": False},
        "alignment": {"model": "remote/model", "offline_model_cache": False},
    }


class RunContextTests(unittest.TestCase):
    def test_prepares_overrides_paths_environment_and_diagnostics_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            input_path = root / "video.mkv"
            input_path.touch()
            sidecar_dir = root / "sidecars"
            config = _config()
            args = _arguments(
                input=str(input_path),
                workflow="hosted-long-stream",
                config="custom.json",
                env_file="keys.env",
                profile=True,
                audio_track=3,
                sidecar_dir=str(sidecar_dir),
            )
            with mock.patch("subtitler.run_context.load_workflow_config", return_value=config) as load_config, mock.patch(
                "subtitler.run_context.validate_workflow_config"
            ) as validate, mock.patch(
                "subtitler.run_context.load_env_file", return_value=["OPENAI_API_KEY"]
            ) as load_env, mock.patch(
                "subtitler.run_context.configure_alignment_offline_mode", return_value=False
            ) as configure_offline:
                context = prepare_run_context(args, cwd=root)

        self.assertIsInstance(context, RunContext)
        self.assertEqual(context.input_path, input_path)
        self.assertEqual(context.output_path, root / "video-long-stream-hosted.exo")
        self.assertEqual(context.config_path, Path("custom.json"))
        self.assertEqual(context.env_path, root / "keys.env")
        self.assertEqual(context.loaded_env_keys, ["OPENAI_API_KEY"])
        self.assertTrue(context.sidecars_enabled)
        self.assertTrue(context.diagnostics_enabled)
        self.assertEqual(context.artifacts.directory, sidecar_dir)
        self.assertEqual(context.artifacts.run_metadata, sidecar_dir / "video-long-stream-hosted.run.json")
        self.assertEqual(config["audio"]["track"], 3)
        self.assertTrue(config["diagnostics"]["profile"])
        load_config.assert_called_once_with("hosted-long-stream", Path("custom.json"))
        validate.assert_called_once_with(config, workflow="hosted-long-stream")
        load_env.assert_called_once_with(root / "keys.env")
        configure_offline.assert_called_once_with(config["alignment"])

    def test_no_sidecars_disables_artifacts_and_diagnostics_even_when_profiled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            input_path = root / "video.mkv"
            input_path.touch()
            config = _config()
            args = _arguments(input=str(input_path), profile=True, no_sidecars=True)
            with mock.patch("subtitler.run_context.load_workflow_config", return_value=config), mock.patch(
                "subtitler.run_context.validate_workflow_config"
            ), mock.patch("subtitler.run_context.load_env_file", return_value=[]), mock.patch(
                "subtitler.run_context.configure_alignment_offline_mode", return_value=False
            ):
                context = prepare_run_context(args, cwd=root)

        self.assertFalse(context.sidecars_enabled)
        self.assertFalse(context.diagnostics_enabled)
        self.assertIsNone(context.artifacts.directory)
        self.assertIsNone(context.artifacts.run_metadata)

    def test_missing_input_fails_before_config_or_environment_loading(self) -> None:
        args = _arguments(input="missing-input.mkv")
        with mock.patch("subtitler.run_context.load_workflow_config") as load_config, mock.patch(
            "subtitler.run_context.load_env_file"
        ) as load_env:
            with self.assertRaisesRegex(SubtitlerError, "input file not found: missing-input.mkv"):
                prepare_run_context(args)
        load_config.assert_not_called()
        load_env.assert_not_called()


if __name__ == "__main__":
    unittest.main()
