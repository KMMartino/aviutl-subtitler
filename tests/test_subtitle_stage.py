import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.api_usage import ApiUsageLedger
from subtitler.glossary import GlossaryEntry
from subtitler.models import AlignedChunk, AudioChunk, ExoMarker, Subtitle
from subtitler.run_artifacts import build_run_artifact_paths
from subtitler.run_context import CliArguments, RunContext
from subtitler.subtitle_stage import build_refiner, run_subtitle_stage
from subtitler.text_refiner import TextRefiner


def _context(root: Path, *, workflow: str, backend: str, skip_review: bool, chapters: bool) -> RunContext:
    input_path = root / "input.mkv"
    input_path.touch()
    output_path = root / "output.exo"
    args = CliArguments(
        input=str(input_path),
        workflow=workflow,
        output=str(output_path),
        config=None,
        env_file=".env",
        profile=True,
        audio_track=None,
        sidecar_dir=str(root / "sidecars"),
        no_sidecars=False,
        glossary=None,
        no_glossary=False,
    )
    artifacts = build_run_artifact_paths(
        input_path,
        output_path,
        enabled=True,
        directory=root / "sidecars",
    )
    return RunContext(
        args=args,
        input_path=input_path,
        output_path=output_path,
        config_path=root / "config.json",
        config={
            "backend": {"transcriber": "local-gemma" if workflow == "local" else "openai", "n_gpu_layers": 22},
            "cleanup": {
                "backend": backend,
                "model": str(root / "cleanup.gguf"),
                "llama_server": str(root / "llama-server.exe"),
                "server_port": 8082,
                "ctx_size": 4096,
                "spec_draft_model": "",
                "spec_draft_n_max": 16,
                "api_model": "hosted-cleanup",
                "llm_split_planning": True,
                "window_subtitles": 0,
                "workers": 0,
                "skip_final_review": skip_review,
            },
            "subtitles": {
                "max_chars": 32,
                "min_duration": 0.4,
                "max_duration": 6.0,
                "gap_threshold": 0.3,
                "regroup_gap_sec": 1.2,
                "chain_lead_in_sec": -0.5,
                "chain_split_workers": 0,
            },
            "diagnostics": {"llm_split_diagnostics": True},
            "additional_settings": {"youtube_chapters": chapters},
        },
        env_path=root / ".env",
        loaded_env_keys=[],
        sidecars_enabled=True,
        diagnostics_enabled=True,
        artifacts=artifacts,
    )


def _aligned() -> list[AlignedChunk]:
    chunk = AudioChunk(1, 0.0, 1.0, [])
    return [AlignedChunk(chunk, "元字幕", [], fallback=True)]


class SubtitleStageContractTests(unittest.TestCase):
    def test_local_stage_passes_planning_contract_writes_text_skips_review_and_closes(self) -> None:
        subtitles = [Subtitle(0.0, 1.0, "字幕")]
        refiner = mock.Mock(spec=TextRefiner)
        factory = mock.Mock(return_value=refiner)
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(
                root,
                workflow="local",
                backend="local-llama",
                skip_review=True,
                chapters=True,
            )
            output = io.StringIO()
            with mock.patch(
                "subtitler.subtitle_stage.build_grouped_subtitles", return_value=subtitles
            ) as planner, mock.patch(
                "subtitler.subtitle_stage.build_youtube_chapter_markers"
            ) as chapters, mock.patch(
                "subtitler.subtitle_stage.flag_possible_mistranscriptions"
            ) as review, contextlib.redirect_stdout(output):
                result = run_subtitle_stage(
                    context,
                    _aligned(),
                    [GlossaryEntry("用語")],
                    ApiUsageLedger(),
                    refiner_factory=factory,
                )
            final_text = context.artifacts.final_text.read_text(encoding="utf-8")

        kwargs = planner.call_args.kwargs
        self.assertEqual(result.subtitles, subtitles)
        self.assertEqual(result.chapter_markers, [])
        self.assertEqual(result.mistranscription_markers, [])
        self.assertIs(kwargs["refiner"], refiner)
        self.assertIs(kwargs["llm_splitter"], refiner)
        self.assertEqual(kwargs["regroup_profile_path"], context.artifacts.regroup_profile)
        self.assertEqual(kwargs["llm_split_profile_path"], context.artifacts.llm_split_profile)
        self.assertTrue(kwargs["llm_split_console"])
        self.assertEqual(kwargs["subtitle_timing_profile_path"], context.artifacts.subtitle_timing_profile)
        self.assertEqual(kwargs["boundary_timing_profile_path"], context.artifacts.boundary_timing_profile)
        self.assertEqual(kwargs["cleanup_diff_path"], context.artifacts.cleanup_diff)
        self.assertEqual(kwargs["planning_profile_path"], context.artifacts.planning_profile)
        self.assertEqual(kwargs["chain_lead_in_sec"], 0.0)
        self.assertEqual(kwargs["cleanup_window_subtitles"], 1)
        self.assertEqual(kwargs["cleanup_workers"], 1)
        self.assertEqual(kwargs["chain_split_workers"], 1)
        self.assertTrue(callable(kwargs["progress_callback"]))
        self.assertIn("1. 字幕", final_text)
        self.assertIn("Skipping final mistranscription check.", output.getvalue())
        chapters.assert_not_called()
        review.assert_not_called()
        refiner.close.assert_called_once_with()

    def test_hosted_stage_builds_chapters_runs_review_returns_markers_and_closes(self) -> None:
        subtitles = [Subtitle(0.0, 1.0, "字幕")]
        chapter_marker = ExoMarker(0.0, 1.0, "Chapter")
        review_marker = ExoMarker(0.0, 1.0, "Review")
        refiner = mock.Mock(spec=TextRefiner)
        factory = mock.Mock(return_value=refiner)
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(
                root,
                workflow="hosted",
                backend="openai",
                skip_review=False,
                chapters=True,
            )
            output = io.StringIO()
            with mock.patch(
                "subtitler.subtitle_stage.build_grouped_subtitles", return_value=subtitles
            ) as planner, mock.patch(
                "subtitler.subtitle_stage.build_youtube_chapter_markers", return_value=[chapter_marker]
            ) as chapters, mock.patch(
                "subtitler.subtitle_stage.flag_possible_mistranscriptions", return_value=[review_marker]
            ) as review, contextlib.redirect_stdout(output):
                result = run_subtitle_stage(
                    context,
                    _aligned(),
                    [],
                    ApiUsageLedger(),
                    refiner_factory=factory,
                )

        kwargs = planner.call_args.kwargs
        self.assertEqual(kwargs["cleanup_window_subtitles"], 256)
        self.assertEqual(kwargs["cleanup_workers"], 8)
        self.assertEqual(kwargs["chain_split_workers"], 6)
        chapters.assert_called_once_with(subtitles, refiner, context.artifacts.chapter_markers)
        review.assert_called_once_with(subtitles, refiner, context.artifacts.mistranscriptions)
        self.assertEqual(result.chapter_markers, [chapter_marker])
        self.assertEqual(result.mistranscription_markers, [review_marker])
        self.assertIn("Running final mistranscription check...", output.getvalue())
        refiner.close.assert_called_once_with()

    def test_refiner_closes_when_planning_raises(self) -> None:
        refiner = mock.Mock(spec=TextRefiner)
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(
                root,
                workflow="local",
                backend="local-llama",
                skip_review=False,
                chapters=False,
            )
            with mock.patch(
                "subtitler.subtitle_stage.build_grouped_subtitles", side_effect=RuntimeError("planning failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "planning failed"):
                    run_subtitle_stage(
                        context,
                        _aligned(),
                        [],
                        ApiUsageLedger(),
                        refiner_factory=mock.Mock(return_value=refiner),
                    )
        refiner.close.assert_called_once_with()


class SubtitleRefinerFactoryTests(unittest.TestCase):
    def test_local_factory_preserves_server_and_sidecar_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            context = _context(
                root,
                workflow="local",
                backend="local-llama",
                skip_review=False,
                chapters=False,
            )
            with mock.patch("subtitler.subtitle_stage.LlamaServerTextRefiner") as refiner_type:
                build_refiner(context.config, [], ApiUsageLedger(), context.artifacts.base)

        kwargs = refiner_type.call_args.kwargs
        self.assertEqual(kwargs["model_path"], root / "cleanup.gguf")
        self.assertEqual(kwargs["server_path"], root / "llama-server.exe")
        self.assertEqual(kwargs["port"], 8082)
        self.assertEqual(kwargs["ctx_size"], 4096)
        self.assertEqual(kwargs["n_gpu_layers"], 22)
        self.assertEqual(kwargs["log_path"], context.artifacts.base.with_suffix(".cleanup_llama.log"))
        self.assertEqual(
            kwargs["cleanup_diagnostics_path"],
            context.artifacts.base.with_suffix(".cleanup_rejections.jsonl"),
        )

    def test_hosted_factory_preserves_model_glossary_and_usage(self) -> None:
        glossary = [GlossaryEntry("用語")]
        usage = ApiUsageLedger()
        with tempfile.TemporaryDirectory() as temp_name:
            context = _context(
                Path(temp_name),
                workflow="hosted",
                backend="openai",
                skip_review=False,
                chapters=False,
            )
            with mock.patch("subtitler.subtitle_stage.OpenAITextRefiner") as refiner_type:
                build_refiner(context.config, glossary, usage, context.artifacts.base)
        refiner_type.assert_called_once_with(model="hosted-cleanup", glossary=glossary, usage=usage)


if __name__ == "__main__":
    unittest.main()
