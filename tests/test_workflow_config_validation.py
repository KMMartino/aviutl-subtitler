import unittest

from subtitler.config import WORKFLOWS, load_workflow_config, validate_workflow_config
from subtitler.errors import SubtitlerError


class WorkflowConfigValidationTests(unittest.TestCase):
    def assert_invalid_field(self, section, field, value, *, workflow="hosted"):
        config = load_workflow_config(workflow)
        config[section][field] = value
        with self.assertRaises(SubtitlerError):
            validate_workflow_config(config, workflow=workflow, check_paths=False)

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
            ("openai", "gpt-5.6-sol"),
            ("openai", "gpt-5.6-terra"),
            ("openai", "gpt-5.6-luna"),
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
            ("openai", "gpt-4o-mini-transcribe-2025-12-15"),
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
        config["backend"]["fallback_transcriber"] = "gemini"
        config["backend"]["fallback_transcription_model"] = "gemini-3.1-pro-preview"

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

    def test_cut_silence_modes_are_allowed_for_short_workflows(self):
        for workflow in ("local", "hosted"):
            for mode in ("automatic", "review"):
                with self.subTest(workflow=workflow, mode=mode):
                    config = load_workflow_config(workflow)
                    config["additional_settings"]["cut_silence_mode"] = mode
                    validate_workflow_config(config, workflow=workflow, check_paths=False)

    def test_cut_silence_is_rejected_for_long_stream_workflows(self):
        for workflow in ("local-long-stream", "hosted-long-stream"):
            config = load_workflow_config(workflow)
            config["additional_settings"]["cut_silence_mode"] = "automatic"
            with self.assertRaises(SubtitlerError):
                validate_workflow_config(config, workflow=workflow, check_paths=False)

    def test_invalid_cut_silence_mode_is_rejected(self):
        self.assert_invalid_field("additional_settings", "cut_silence_mode", "sometimes")

    def test_render_cut_video_is_allowed_only_for_short_workflows(self):
        for workflow in ("local", "hosted"):
            config = load_workflow_config(workflow)
            config["additional_settings"]["render_cut_video"] = True
            validate_workflow_config(config, workflow=workflow, check_paths=False)
        for workflow in ("local-long-stream", "hosted-long-stream"):
            config = load_workflow_config(workflow)
            config["additional_settings"]["render_cut_video"] = True
            with self.assertRaises(SubtitlerError):
                validate_workflow_config(config, workflow=workflow, check_paths=False)

    def test_boolean_fields_require_actual_booleans(self):
        fields = (
            ("alignment", "offline_model_cache"),
            ("cleanup", "skip_final_review"),
            ("cleanup", "llm_split_planning"),
            ("diagnostics", "profile"),
            ("diagnostics", "llm_split_diagnostics"),
            ("cost", "allow_api_spend"),
            ("cost", "estimate_cost_only"),
            ("additional_settings", "youtube_chapters"),
            ("additional_settings", "render_cut_video"),
        )
        for section, field in fields:
            with self.subTest(field=f"{section}.{field}"):
                self.assert_invalid_field(section, field, "false")

    def test_integer_fields_reject_booleans(self):
        fields = (
            ("backend", "server_port"),
            ("backend", "n_gpu_layers"),
            ("backend", "ctx_size"),
            ("backend", "audio_prep_workers"),
            ("backend", "transcription_workers"),
            ("backend", "transcription_max_split_depth"),
            ("backend", "spec_draft_n_max"),
            ("audio", "track"),
            ("workflow", "long_stream_min_chunks"),
            ("vad", "min_silence_ms"),
            ("vad", "speech_pad_ms"),
            ("alignment", "max_split_depth"),
            ("alignment", "workers"),
            ("alignment", "torch_threads"),
            ("alignment", "emission_batch_size"),
            ("cleanup", "server_port"),
            ("cleanup", "ctx_size"),
            ("cleanup", "window_subtitles"),
            ("cleanup", "workers"),
            ("cleanup", "spec_draft_n_max"),
            ("subtitles", "max_chars"),
            ("subtitles", "chain_split_workers"),
            ("exo", "width"),
            ("exo", "height"),
            ("exo", "fps"),
            ("exo", "font_size"),
        )
        for section, field in fields:
            with self.subTest(field=f"{section}.{field}"):
                self.assert_invalid_field(section, field, True)

    def test_non_finite_numeric_fields_are_rejected(self):
        fields = (
            ("workflow", "long_stream_selection_ratio"),
            ("vad", "max_chunk_sec"),
            ("vad", "min_speech_sec"),
            ("vad", "min_silence_ms"),
            ("vad", "speech_pad_ms"),
            ("subtitles", "min_duration"),
            ("subtitles", "max_duration"),
            ("subtitles", "gap_threshold"),
            ("subtitles", "regroup_gap_sec"),
            ("subtitles", "chain_lead_in_sec"),
            ("exo", "y_position"),
            ("cost", "max_estimated_api_cost_usd"),
        )
        for section, field in fields:
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=f"{section}.{field}", value=value):
                    self.assert_invalid_field(section, field, value)

    def test_worker_counts_depths_batches_and_contexts_are_range_checked(self):
        fields = (
            ("backend", "ctx_size", 0),
            ("backend", "audio_prep_workers", 0),
            ("backend", "transcription_workers", 0),
            ("backend", "transcription_max_split_depth", -1),
            ("backend", "spec_draft_n_max", 0),
            ("vad", "min_silence_ms", 0),
            ("vad", "speech_pad_ms", -1),
            ("alignment", "max_split_depth", -1),
            ("alignment", "workers", 0),
            ("alignment", "torch_threads", 0),
            ("alignment", "emission_batch_size", 0),
            ("cleanup", "ctx_size", 0),
            ("cleanup", "window_subtitles", 0),
            ("cleanup", "workers", 0),
            ("cleanup", "spec_draft_n_max", 0),
            ("subtitles", "chain_split_workers", 0),
        )
        for section, field, value in fields:
            with self.subTest(field=f"{section}.{field}"):
                self.assert_invalid_field(section, field, value)

    def test_vad_millisecond_fields_reject_fractional_values(self):
        for field in ("min_silence_ms", "speech_pad_ms"):
            with self.subTest(field=field):
                self.assert_invalid_field("vad", field, 1.5)

    def test_server_ports_must_be_in_tcp_port_range(self):
        for section in ("backend", "cleanup"):
            for value in (0, 65536):
                with self.subTest(section=section, value=value):
                    self.assert_invalid_field(section, "server_port", value)

    def test_optional_numeric_fields_allow_none(self):
        config = load_workflow_config("hosted")
        for section, field in (
            ("backend", "transcription_workers"),
            ("workflow", "long_stream_selection_ratio"),
            ("alignment", "workers"),
            ("alignment", "torch_threads"),
            ("cleanup", "window_subtitles"),
            ("cleanup", "workers"),
            ("subtitles", "chain_split_workers"),
        ):
            config[section][field] = None

        validate_workflow_config(config, workflow="hosted", check_paths=False)


if __name__ == "__main__":
    unittest.main()
