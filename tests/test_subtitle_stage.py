import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subtitler.api_usage import ApiUsageLedger
from subtitler.glossary import GlossaryEntry
from subtitler.models import AlignedChunk, AlignedToken, AudioChunk, ExoMarker, Subtitle
from subtitler.run_artifacts import build_run_artifact_paths
from subtitler.subtitle_stage import SubtitleStageRequest, build_refiner, run_subtitle_stage
from subtitler.workflow_policy import workflow_definition
from subtitler.text_refiner import TextRefiner


def _context(root: Path, *, workflow: str, backend: str, skip_review: bool, chapters: bool) -> SubtitleStageRequest:
    input_path = root / "input.mkv"
    input_path.touch()
    output_path = root / "output.exo"
    artifacts = build_run_artifact_paths(
        input_path,
        output_path,
        enabled=True,
        directory=root / "sidecars",
    )
    policy = workflow_definition(workflow).subtitles
    return SubtitleStageRequest(
        raw_transcript=policy.raw_transcript,
        generate_chapters=policy.allow_chapters and chapters,
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
        sidecars_enabled=True,
        diagnostics_enabled=True,
        artifacts=artifacts,
    )


def _aligned() -> list[AlignedChunk]:
    chunk = AudioChunk(1, 0.0, 1.0, [])
    return [AlignedChunk(chunk, "元字幕", [], fallback=True)]


def _long_aligned() -> list[AlignedChunk]:
    text = "これは意味のまとまりを保ったまま表示用字幕だけを適切な長さに分割する長い発話です。"
    duration = 12.0
    step = duration / len(text)
    tokens = [
        AlignedToken(character, index * step, (index + 1) * step, "char")
        for index, character in enumerate(text)
    ]
    return [AlignedChunk(AudioChunk(1, 0.0, duration, []), text, tokens)]


class SubtitleStageContractTests(unittest.TestCase):
    def test_hosted_long_stream_persists_raw_transcript_without_full_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            context = _context(
                Path(temp_name),
                workflow="hosted-long-stream",
                backend="openai",
                skip_review=False,
                chapters=False,
            )
            factory = mock.Mock()
            result = run_subtitle_stage(
                context,
                _aligned(),
                [],
                ApiUsageLedger(),
                refiner_factory=factory,
            )
            self.assertTrue(context.artifacts.subtitle_timing_profile.is_file())
            self.assertIn(
                "元字幕", context.artifacts.final_text.read_text(encoding="utf-8")
            )
        factory.assert_not_called()
        self.assertEqual(len(result.subtitles), 1)

    def test_hosted_long_stream_splits_on_tokens_without_clipping_them(self) -> None:
        aligned = _long_aligned()
        with tempfile.TemporaryDirectory() as temp_name:
            context = _context(
                Path(temp_name),
                workflow="hosted-long-stream",
                backend="openai",
                skip_review=False,
                chapters=False,
            )
            result = run_subtitle_stage(
                context,
                aligned,
                [],
                ApiUsageLedger(),
                refiner_factory=mock.Mock(),
            )

        self.assertGreater(len(result.subtitles), 1)
        self.assertEqual(
            "".join(token.text for subtitle in result.subtitles for token in subtitle.tokens),
            aligned[0].text,
        )
        self.assertTrue(
            all(
                subtitle.end_time >= max(token.end for token in subtitle.tokens)
                for subtitle in result.subtitles
                if subtitle.tokens
            )
        )
        self.assertTrue(all(subtitle.end_time - subtitle.start_time <= 6.0 for subtitle in result.subtitles))

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
    def test_selects_requested_adapter_and_preserves_glossary(self):
        glossary = [GlossaryEntry("用語")]
        with tempfile.TemporaryDirectory() as directory:
            for workflow, backend, adapter in [("local", "local-llama", "LlamaServerTextRefiner"), ("hosted", "openai", "OpenAITextRefiner")]:
                with self.subTest(backend=backend), mock.patch(f"subtitler.subtitle_stage.{adapter}") as factory:
                    context = _context(Path(directory), workflow=workflow, backend=backend, skip_review=False, chapters=False)
                    usage = ApiUsageLedger()
                    result = build_refiner(context.config, glossary, usage, context.artifacts.base)
                    self.assertIs(result, factory.return_value)
                    self.assertIs(factory.call_args.kwargs["glossary"], glossary)
                    if workflow == "hosted":
                        self.assertIs(factory.call_args.kwargs["usage"], usage)


if __name__ == "__main__":
    unittest.main()
