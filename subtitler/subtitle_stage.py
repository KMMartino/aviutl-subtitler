"""Subtitle planning, cleanup, and review pipeline stage."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .api_usage import ApiUsageLedger
from .errors import SubtitlerError
from .external_refiners import GeminiTextRefiner, OpenAITextRefiner
from .glossary import GlossaryEntry
from .models import AlignedChunk, ExoMarker, Subtitle
from .run_artifacts import (
    build_youtube_chapter_markers,
    flag_possible_mistranscriptions,
    write_final_subtitle_text,
)
from .run_context import RunContext
from .subtitle_planner import build_grouped_subtitles
from .text_refiner import LlamaServerTextRefiner, TextRefiner


class RefinerFactory(Protocol):
    def __call__(
        self,
        config: dict,
        glossary: list[GlossaryEntry],
        api_usage: ApiUsageLedger,
        sidecar_base: Path | None,
    ) -> TextRefiner | None: ...


@dataclass(frozen=True)
class SubtitleStageOutcome:
    subtitles: list[Subtitle]
    chapter_markers: list[ExoMarker]
    mistranscription_markers: list[ExoMarker]


def run_subtitle_stage(
    context: RunContext,
    aligned: list[AlignedChunk],
    glossary: list[GlossaryEntry],
    api_usage: ApiUsageLedger,
    *,
    refiner_factory: RefinerFactory | None = None,
) -> SubtitleStageOutcome:
    """Plan and refine subtitles, always closing an initialized refiner."""
    config = context.config
    artifacts = context.artifacts
    cleanup_cfg = config["cleanup"]
    subtitle_cfg = config["subtitles"]
    refiner = (refiner_factory or build_refiner)(config, glossary, api_usage, artifacts.base)
    try:
        chapter_markers: list[ExoMarker] = []
        mistranscription_markers: list[ExoMarker] = []
        subtitles = build_grouped_subtitles(
            aligned,
            max_chars=int(subtitle_cfg["max_chars"]),
            min_duration=float(subtitle_cfg["min_duration"]),
            max_duration=float(subtitle_cfg["max_duration"]),
            gap_threshold=float(subtitle_cfg["gap_threshold"]),
            regroup_gap_sec=float(subtitle_cfg["regroup_gap_sec"]),
            refiner=refiner,
            llm_splitter=refiner if cleanup_cfg.get("llm_split_planning") else None,
            regroup_profile_path=artifacts.regroup_profile if context.diagnostics_enabled else None,
            llm_split_profile_path=(
                artifacts.llm_split_profile
                if context.sidecars_enabled and config["diagnostics"]["llm_split_diagnostics"]
                else None
            ),
            llm_split_console=bool(
                context.sidecars_enabled and config["diagnostics"]["llm_split_diagnostics"]
            ),
            subtitle_timing_profile_path=(
                artifacts.subtitle_timing_profile if context.diagnostics_enabled else None
            ),
            boundary_timing_profile_path=(
                artifacts.boundary_timing_profile if context.diagnostics_enabled else None
            ),
            cleanup_diff_path=artifacts.cleanup_diff if context.sidecars_enabled else None,
            chain_lead_in_sec=max(0.0, float(subtitle_cfg["chain_lead_in_sec"])),
            cleanup_window_subtitles=int(
                cleanup_cfg["window_subtitles"] or default_cleanup_window(config)
            ),
            cleanup_workers=int(cleanup_cfg["workers"] or default_cleanup_workers(config)),
            chain_split_workers=int(
                subtitle_cfg["chain_split_workers"] or default_chain_split_workers(config)
            ),
            progress_callback=count_progress_reporter(),
            planning_profile_path=artifacts.planning_profile if context.diagnostics_enabled else None,
        )
        if refiner is not None:
            if artifacts.final_text is not None:
                write_final_subtitle_text(artifacts.final_text, subtitles)
            if context.args.workflow == "hosted" and config["additional_settings"]["youtube_chapters"]:
                chapter_markers = build_youtube_chapter_markers(
                    subtitles,
                    refiner,
                    artifacts.chapter_markers if context.sidecars_enabled else None,
                )
            if cleanup_cfg.get("skip_final_review"):
                print("Skipping final mistranscription check.", flush=True)
            else:
                print("Running final mistranscription check...", flush=True)
                if artifacts.mistranscriptions is not None:
                    mistranscription_markers = flag_possible_mistranscriptions(
                        subtitles,
                        refiner,
                        artifacts.mistranscriptions,
                    )
        return SubtitleStageOutcome(subtitles, chapter_markers, mistranscription_markers)
    finally:
        if refiner is not None:
            refiner.close()


def build_refiner(
    config: dict,
    glossary: list[GlossaryEntry],
    api_usage: ApiUsageLedger,
    sidecar_base: Path | None,
) -> TextRefiner | None:
    cleanup = config["cleanup"]
    backend = cleanup["backend"]
    if backend == "none":
        return None
    if backend == "local-llama":
        if not cleanup.get("model"):
            raise SubtitlerError("local cleanup requires cleanup.model")
        print("Starting cleanup model...")
        return LlamaServerTextRefiner(
            model_path=Path(cleanup["model"]),
            server_path=Path(cleanup["llama_server"]) if cleanup.get("llama_server") else None,
            glossary=glossary,
            mode="full",
            host="127.0.0.1",
            port=int(cleanup["server_port"]),
            ctx_size=int(cleanup["ctx_size"]),
            n_gpu_layers=int(config["backend"]["n_gpu_layers"]),
            spec_draft_model=(
                Path(cleanup["spec_draft_model"]) if cleanup.get("spec_draft_model") else None
            ),
            spec_draft_n_max=int(cleanup["spec_draft_n_max"]),
            log_path=(
                sidecar_base.with_suffix(".cleanup_llama.log")
                if sidecar_base is not None
                else Path(tempfile.gettempdir()) / f"subtitler_cleanup_llama_{os.getpid()}.log"
            ),
            cleanup_diagnostics_path=(
                sidecar_base.with_suffix(".cleanup_rejections.jsonl")
                if sidecar_base is not None
                else None
            ),
        )
    if backend == "openai":
        return OpenAITextRefiner(
            model=cleanup["api_model"],
            glossary=glossary,
            usage=api_usage,
            reasoning_effort=cleanup.get("reasoning_effort"),
        )
    if backend == "gemini":
        return GeminiTextRefiner(
            model=cleanup["api_model"],
            glossary=glossary,
            usage=api_usage,
            thinking_level=cleanup.get("thinking_level"),
        )
    raise SubtitlerError(f"Unknown cleanup backend: {backend}")


def default_cleanup_window(config: dict) -> int:
    return 256 if config["cleanup"]["backend"] in {"gemini", "openai"} else 1


def default_cleanup_workers(config: dict) -> int:
    return 8 if config["cleanup"]["backend"] in {"gemini", "openai"} else 1


def default_chain_split_workers(config: dict) -> int:
    return 6 if config["backend"]["transcriber"] != "local-gemma" else 1


def count_progress_reporter(step: int = 10):
    """Report coarse stage progress without turning the normal UI into a debug log."""
    last_percent: dict[str, int] = {}

    def report(stage: str, completed: int, total: int) -> None:
        if total <= 0:
            return
        percent = min(100, max(0, int(completed * 100 / total)))
        previous = last_percent.get(stage, -step)
        if completed == 0 or completed == total or percent >= previous + step:
            print(f"{stage}: {completed}/{total}", flush=True)
            last_percent[stage] = percent

    return report
