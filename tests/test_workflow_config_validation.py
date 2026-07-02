import unittest

from subtitler.config import WORKFLOWS, load_workflow_config, validate_workflow_config
from subtitler.errors import SubtitlerError


class WorkflowConfigValidationTests(unittest.TestCase):
    def test_all_default_configs_validate_without_path_checks(self):
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow):
                validate_workflow_config(load_workflow_config(workflow), workflow=workflow, check_paths=False)

    def test_unknown_backend_is_rejected(self):
        config = load_workflow_config("local")
        config["backend"]["name"] = "old-pipeline"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=False)

    def test_wrong_workflow_backend_pairing_is_rejected(self):
        config = load_workflow_config("local")
        config["backend"]["transcriber"] = "gemini"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=False)

    def test_local_missing_model_path_is_rejected(self):
        config = load_workflow_config("local")
        config["backend"]["model"] = ""

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=False)

    def test_local_missing_model_file_is_rejected_when_checking_paths(self):
        config = load_workflow_config("local")
        config["backend"]["model"] = "C:/definitely/missing/model.gguf"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=True)

    def test_hosted_missing_transcription_model_is_rejected(self):
        config = load_workflow_config("hosted")
        config["backend"]["transcription_model"] = ""

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_hosted_openai_transcription_and_gemini_cleanup_are_allowed(self):
        config = load_workflow_config("hosted")
        config["backend"]["transcriber"] = "openai"
        config["backend"]["transcription_model"] = "gpt-4o-transcribe"
        config["cleanup"]["backend"] = "gemini"
        config["cleanup"]["api_model"] = "gemini-3.5-flash"

        validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_unapproved_hosted_model_is_rejected(self):
        config = load_workflow_config("hosted")
        config["backend"]["transcription_model"] = "gemini-something-else"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_new_approved_cleanup_models_are_allowed(self):
        for backend, model in (
            ("openai", "gpt-5.5"),
            ("gemini", "gemini-3.1-pro-preview"),
            ("gemini", "gemini-3.1-flash-lite"),
        ):
            with self.subTest(model=model):
                config = load_workflow_config("hosted")
                config["cleanup"]["backend"] = backend
                config["cleanup"]["api_model"] = model
                validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_new_approved_transcription_models_are_allowed(self):
        for backend, model in (
            ("openai", "gpt-4o-mini-transcribe"),
            ("gemini", "gemini-3.1-pro-preview"),
            ("gemini", "gemini-3.1-flash-lite"),
        ):
            with self.subTest(model=model):
                config = load_workflow_config("hosted")
                config["backend"]["transcriber"] = backend
                config["backend"]["transcription_model"] = model
                validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_approved_fallback_transcription_models_are_allowed(self):
        config = load_workflow_config("hosted")
        config["backend"]["fallback_transcriber"] = "openai"
        config["backend"]["fallback_transcription_model"] = "gpt-4o-mini-transcribe"

        validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_unapproved_fallback_transcription_model_is_rejected(self):
        config = load_workflow_config("hosted")
        config["backend"]["fallback_transcriber"] = "openai"
        config["backend"]["fallback_transcription_model"] = "gemini-3.5-flash"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_negative_audio_track_is_rejected(self):
        config = load_workflow_config("local")
        config["audio"]["track"] = -1

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=False)

    def test_invalid_workflow_mode_is_rejected(self):
        config = load_workflow_config("local")
        config["workflow"]["mode"] = "preview"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=False)

    def test_invalid_cleanup_backend_is_rejected(self):
        config = load_workflow_config("local")
        config["cleanup"]["backend"] = "legacy"

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local", check_paths=False)

    def test_long_stream_min_chunks_below_zero_is_rejected(self):
        config = load_workflow_config("local-long-stream")
        config["workflow"]["long_stream_min_chunks"] = -1

        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow="local-long-stream", check_paths=False)

    def test_hosted_short_youtube_chapters_are_allowed(self):
        config = load_workflow_config("hosted")
        config["additional_settings"]["youtube_chapters"] = True

        validate_workflow_config(config, workflow="hosted", check_paths=False)

    def test_youtube_chapters_are_rejected_outside_hosted_short(self):
        for workflow in ("local", "local-long-stream", "hosted-long-stream"):
            with self.subTest(workflow=workflow):
                config = load_workflow_config(workflow)
                config["additional_settings"]["youtube_chapters"] = True
                with self.assertRaises(SubtitlerError):
                    validate_workflow_config(config, workflow=workflow, check_paths=False)


if __name__ == "__main__":
    unittest.main()
