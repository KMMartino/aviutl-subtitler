"""Concrete hosted stages for the checkpointed editorial project runner."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .api_usage import ApiUsageLedger
from .audio import get_media_duration
from .config import load_workflow_config, validate_workflow_config
from .editorial_analysis import (
    EDITORIAL_PROMPT_VERSION,
    TranscriptEvidence,
    VisualEvidence,
    analyze_editorial_source,
    select_editorial_subtitles,
    synthesize_human_information_project,
)
from .editorial_cutting import build_human_information_plan
from .editorial_assets import (
    OpenAIEditorialEvidenceProvider,
    resolve_editorial_assets,
)
from .editorial_project import EDITORIAL_STAGE_VERSIONS
from .editorial_enrichment import (
    analyze_acoustic_emphasis,
)
from .editorial_locale import locale_label
from .env import load_env_file
from .errors import SubtitlerError
from .game_knowledge import (
    game_profile_context,
    load_game_profile,
    update_game_profile,
)
from .glossary import load_glossary
from .media_layout import analyze_wide_recording, probe_video_geometry
from .game_wiki import lookup_game_wiki
from .editorial_visual import OpenAIEditorialVisualProvider
from .media_analysis import (
    AnalysisSegment,
    MediaAnalysisResponseError,
    MediaAnalysisResult,
    analyze_media,
)
from .subtitle_stage import build_refiner


EDITORIAL_PROGRESS_FIRST_UPDATE_SECONDS = 20.0
EDITORIAL_PROGRESS_UPDATE_INTERVAL_SECONDS = 30.0
EDITORIAL_VISUAL_WINDOW_SECONDS = 12 * 60.0
MAX_EDITORIAL_VISUAL_WORKERS = 3
MAX_EDITORIAL_VISUAL_SPLIT_DEPTH = 2
EDITORIAL_SUBTITLE_TARGET_CHARS = 20
EDITORIAL_SUBTITLE_MAX_CHARS = 40
EDITORIAL_SUBTITLE_MIN_CHARS = 6


@dataclass(frozen=True)
class HostedEditorialExecutorOptions:
    config_path: Path
    env_file: Path
    workspace: Path
    pipeline_script: Path
    audio_track: int = 1
    glossary_path: Path | None = None
    game_knowledge_path: Path | None = None


class HostedEditorialStageExecutor:
    """Run generic transcript, visual, and semantic stages for one source."""

    def __init__(self, options: HostedEditorialExecutorOptions) -> None:
        self.options = options
        self.options.workspace.mkdir(parents=True, exist_ok=True)
        load_env_file(options.env_file)
        self.config = load_workflow_config("hosted-long-stream", options.config_path)
        validate_workflow_config(
            self.config,
            workflow="hosted-long-stream",
            check_paths=False,
        )

    def run_stage(
        self,
        stage: str,
        source: dict[str, Any],
        project: dict[str, Any],
        prior_outputs: dict[str, Any],
    ) -> Any:
        if stage == "source_probe":
            return self._probe(source)
        if stage == "transcription":
            return self._transcribe(source, project)
        if stage == "visual_learning":
            return self._analyze_visuals(source, project, prior_outputs)
        if stage == "semantic_spans":
            return self._analyze_semantics(source, project, prior_outputs)
        if stage == "local_reconciliation":
            return self._reconcile(prior_outputs)
        raise SubtitlerError(f"Unsupported hosted editorial stage: {stage}")

    def build_narration_review_provider(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        """Build the hosted structured-text provider used after narration review."""
        return self._build_editorial_refiner(usage, sidecar_base)

    def finalize_project(self, project: dict[str, Any]) -> dict[str, Any]:
        usage = ApiUsageLedger()
        global_checkpoint = project.get("editorial_map", {}).get("global_reconciliation", {})
        prior_failure = global_checkpoint.get("output") if isinstance(global_checkpoint, dict) else None
        base_reconciliation = (
            prior_failure.get("base_reconciliation")
            if isinstance(prior_failure, dict)
            and isinstance(prior_failure.get("base_reconciliation"), dict)
            else None
        )
        try:
            if base_reconciliation is None:
                refiner = self._build_director_refiner(
                    usage, self.options.workspace / "editorial-global"
                )
                if refiner is None or not hasattr(refiner, "complete_structured"):
                    raise SubtitlerError(
                        "Hosted story synthesis requires a structured model"
                    )
                print(
                    _message(
                        project,
                        "Story synthesis: building factual phases, long-horizon threads, and narration briefs...",
                        "ストーリー統合: 事実に基づく展開、長期的なつながり、ナレーション案を作成中…",
                    ),
                    flush=True,
                )
                try:
                    with _hosted_progress_updates(
                        project,
                        english_label="Story synthesis",
                        japanese_label="ストーリー統合",
                    ):
                        base_reconciliation = synthesize_human_information_project(
                            provider=refiner, project=project
                        )
                finally:
                    refiner.close()
            else:
                print(
                    _message(
                        project,
                        "Story synthesis: reusing the completed factual project map.",
                        "ストーリー統合: 完了済みの事実ベースのプロジェクトマップを再利用します。",
                    ),
                    flush=True,
                )
        except Exception as exc:
            setattr(
                exc,
                "editorial_failure_output",
                {
                    "api_cost_usd": usage.total_cost_usd,
                    "api_usage": [row.__dict__ for row in usage.rows],
                    "base_reconciliation": base_reconciliation,
                    "structured_response_diagnostics_paths": [
                        str(
                            self.options.workspace
                            / "editorial-global.structured_responses.jsonl"
                        )
                    ],
                },
            )
            raise
        result = dict(base_reconciliation)
        result["api_cost_usd"] = usage.total_cost_usd
        result["api_usage"] = [row.__dict__ for row in usage.rows]
        print(
            _message(
                project,
                f"Story synthesis: complete with {len(result.get('event_phases', []))} phase(s), "
                f"{len(result.get('global_threads', []))} thread(s), and "
                f"{len(result.get('narration_briefs', []))} narration brief(s).",
                f"ストーリー統合: 展開 {len(result.get('event_phases', []))} 件、"
                f"つながり {len(result.get('global_threads', []))} 件、"
                f"ナレーション案 {len(result.get('narration_briefs', []))} 件を作成しました。",
            ),
            flush=True,
        )
        return result

    def plan_actions(self, project: dict[str, Any]) -> dict[str, Any]:
        """Select display subtitles and deterministically expose human editing guides."""
        usage = ApiUsageLedger()
        synthesis = (
            project.get("editorial_map", {})
            .get("global_reconciliation", {})
            .get("output")
        )
        if not isinstance(synthesis, dict):
            raise SubtitlerError("Human-information planning requires completed story synthesis")
        actionable = build_human_information_plan(project=project, synthesis=synthesis)
        selector = self._build_editorial_refiner(
            usage, self.options.workspace / "editorial-subtitle-selection"
        )
        if selector is None or not hasattr(selector, "complete_structured"):
            raise SubtitlerError("Hosted display-subtitle selection requires a structured model")
        print(
            _message(
                project,
                "Display subtitles: selecting meaningful complete thoughts from the factual story map...",
                "表示字幕: 事実ベースのストーリーマップから意味のある完結した発話を選択中…",
            ),
            flush=True,
        )
        try:
            selected = select_editorial_subtitles(
                provider=selector,
                project=project,
                final_actions=actionable["final_actions"],
                story_actions=actionable["story_actions"],
                progress=lambda message: print(
                    _message(
                        project,
                        f"Display subtitles: {message}",
                        f"表示字幕: {message}",
                    ),
                    flush=True,
                ),
                default_keep=True,
            )
        finally:
            selector.close()

        emphasized_phrases: list[dict[str, Any]] = []
        for source in project.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("source_id") or "")
            transcription = source.get("stages", {}).get("transcription", {}).get("output")
            aligned_tokens_path = (
                transcription.get("aligned_tokens_path")
                if isinstance(transcription, dict)
                else None
            )
            emphasized_phrases.extend(
                _align_emphasized_phrases(
                    [
                        item
                        for item in selected
                        if str(item.get("source_id")) == source_id
                    ],
                    Path(aligned_tokens_path)
                    if isinstance(aligned_tokens_path, str)
                    else None,
                )
            )
        if emphasized_phrases:
            print(
                _message(
                    project,
                    f"Display subtitles: cleaning {len(emphasized_phrases)} timed display beat(s)...",
                    f"表示字幕: タイミング済み表示単位 {len(emphasized_phrases)} 件を整文中…",
                ),
                flush=True,
            )
            cleaner = self._build_subtitle_cleanup_refiner(
                usage, self.options.workspace / "editorial-subtitle-cleanup"
            )
            if cleaner is None:
                raise SubtitlerError(
                    "Hosted display-subtitle cleanup requires a text cleanup model"
                )
            try:
                emphasized_phrases = _clean_selected_editorial_subtitles(
                    emphasized_phrases, cleaner
                )
            finally:
                cleaner.close()
        actionable["emphasized_phrases"] = emphasized_phrases
        result = {
            "director_review": synthesis,
            "director_model": self._director_model(),
            "final_actions": actionable["final_actions"],
            "supporting_edits": [],
            "editorial_threads": actionable["threads"],
            "story_actions": actionable["story_actions"],
            "emphasized_phrases": emphasized_phrases,
            "plan_audit": actionable["plan_audit"],
            "workflow": "human_information",
            "protected_zones": [],
            "cut_candidates": [],
            "confirmed_cuts": actionable["confirmed_cuts"],
            "removed_ms": actionable["removed_ms"],
            "narration_replaced_ms": 0,
            "prompt_version": actionable["prompt_version"],
            "api_cost_usd": usage.total_cost_usd,
            "api_usage": [row.__dict__ for row in usage.rows],
        }
        print(
            _message(
                project,
                f"Human editing guides: complete with {len(result['confirmed_cuts'])} voice-gap marker(s), "
                f"{len(result['final_actions'])} narration brief(s), and "
                f"{len(emphasized_phrases)} display subtitle(s).",
                f"人間向け編集ガイド: 無音マーカー {len(result['confirmed_cuts'])} 件、"
                f"ナレーション案 {len(result['final_actions'])} 件、"
                f"表示字幕 {len(emphasized_phrases)} 件で完了しました。",
            ),
            flush=True,
        )
        return result


    def resolve_assets(self, project: dict[str, Any]) -> dict[str, Any]:
        requests = [
            item
            for item in project.get("editorial_map", {}).get("supporting_edits", [])
            if isinstance(item, dict) and item.get("evidence_request")
        ]
        if not requests:
            print(
                _message(
                    project,
                    "Editorial evidence lookup: no selected suggestion needs a reference asset.",
                    "編集用の根拠画像検索: 参照素材が必要な提案はありません。",
                ),
                flush=True,
            )
            return {
                "prompt_version": "editorial-assets-compatibility-v1",
                "supporting_edits": [],
                "editorial_assets": [],
                "api_cost_usd": 0.0,
                "api_usage": [],
            }
        else:
            print(
                _message(
                    project,
                    f"Editorial evidence lookup: checking {min(len(requests), 16)} selected reference request(s)...",
                    f"編集用の根拠画像検索: 選択された参照候補 {min(len(requests), 16)} 件を確認中…",
                ),
                flush=True,
            )
        provider = OpenAIEditorialEvidenceProvider.from_environment(self._editorial_model())
        return resolve_editorial_assets(
            project,
            workspace=self.options.workspace / "editorial-assets",
            provider=provider,
            output_locale=str(project.get("output_locale", "en")),
        )

    def _editorial_model_config(self) -> dict[str, Any]:
        """Keep editorial intelligence independent from subtitle cleanup tuning."""
        config = json.loads(json.dumps(getattr(self, "config", {})))
        editorial = config.get("editorial")
        if not isinstance(editorial, dict):
            editorial = {}
        cleanup = config.setdefault("cleanup", {})
        cleanup["backend"] = "openai"
        cleanup["api_model"] = str(editorial.get("analysis_model") or "gpt-5.6-luna")
        cleanup["reasoning_effort"] = str(editorial.get("reasoning_effort") or "medium")
        cleanup["thinking_level"] = None
        return config

    def _editorial_model(self) -> str:
        cleanup = self._editorial_model_config()["cleanup"]
        return str(cleanup["api_model"])

    def _visual_reasoning_effort(self) -> str:
        editorial = self.config.get("editorial")
        if not isinstance(editorial, dict):
            editorial = {}
        value = str(editorial.get("visual_reasoning_effort") or "low")
        return value if value in {"none", "low", "medium", "high", "xhigh", "max"} else "low"

    def _director_model_config(self) -> dict[str, Any]:
        """Use the next hosted model tier for the bounded final director pass."""
        config = json.loads(json.dumps(getattr(self, "config", {})))
        editorial = config.get("editorial")
        if not isinstance(editorial, dict):
            editorial = {}
        cleanup = config.setdefault("cleanup", {})
        cleanup["backend"] = "openai"
        cleanup["api_model"] = str(
            editorial.get("director_model") or "gpt-5.6-terra"
        )
        cleanup["reasoning_effort"] = str(
            editorial.get("director_reasoning_effort") or "low"
        )
        cleanup["thinking_level"] = None
        return config



    def _director_model(self) -> str:
        return str(self._director_model_config()["cleanup"]["api_model"])

    def _subtitle_cleanup_model_config(self) -> dict[str, Any]:
        """Use the cleanup-specialized model without leaking it into editorial reasoning."""
        config = json.loads(json.dumps(getattr(self, "config", {})))
        editorial = config.get("editorial")
        if not isinstance(editorial, dict):
            editorial = {}
        cleanup = config.setdefault("cleanup", {})
        cleanup["backend"] = "openai"
        cleanup["api_model"] = str(
            editorial.get("subtitle_cleanup_model") or "gpt-5.6-luna"
        )
        cleanup["reasoning_effort"] = str(
            editorial.get("subtitle_cleanup_reasoning_effort") or "low"
        )
        cleanup["thinking_level"] = None
        return config

    def _build_editorial_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        return build_refiner(self._editorial_model_config(), [], usage, sidecar_base)

    def _build_director_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        return build_refiner(self._director_model_config(), [], usage, sidecar_base)


    def _build_game_learning_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        """Reserve the output budget for the compact profile rather than deliberation."""
        config = self._editorial_model_config()
        config["cleanup"]["reasoning_effort"] = "low"
        return build_refiner(config, [], usage, sidecar_base)

    def _build_subtitle_cleanup_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        return build_refiner(
            self._subtitle_cleanup_model_config(),
            load_glossary(self.options.glossary_path),
            usage,
            sidecar_base,
        )

    def _probe(self, source: dict[str, Any]) -> dict[str, Any]:
        audio_path = Path(source["audio_path"])
        visual_path = Path(source["visual_path"])
        audio_duration = round(get_media_duration(audio_path) * 1000)
        visual_duration = round(get_media_duration(visual_path) * 1000)
        if audio_duration <= 0 or visual_duration <= 0:
            raise SubtitlerError(f"Could not determine paired media duration for {source['source_id']}")
        recorded_duration = int(source["visual_duration_ms"])
        if abs(recorded_duration - visual_duration) > max(2000, recorded_duration * 0.01):
            raise SubtitlerError(
                f"Source duration changed after project creation: {source['original_name']}"
            )
        frame_rate = _probe_frame_rate(visual_path)
        visual_geometry = None
        audio_geometry = None
        wide_layout = None
        try:
            visual_geometry = probe_video_geometry(visual_path)
            if source["media_mode"] == "single":
                wide_layout = analyze_wide_recording(visual_path, visual_geometry)
        except SubtitlerError:
            pass
        if source["media_mode"] == "paired":
            try:
                audio_geometry = probe_video_geometry(audio_path)
            except SubtitlerError:
                pass
        if source["media_mode"] == "paired":
            if frame_rate <= 0:
                raise SubtitlerError(f"Could not determine gameplay frame rate for {source['visual_original_name']}")
            if abs(audio_duration - visual_duration) > (10.0 / frame_rate) * 1000.0 + 1.0:
                raise SubtitlerError(
                    "Facecam and gameplay lengths differ by more than 10 gameplay frames: "
                    f"{source['audio_original_name']} / {source['visual_original_name']}"
                )
        return {
            "duration_ms": visual_duration,
            "audio_duration_ms": audio_duration,
            "visual_duration_ms": visual_duration,
            "frame_rate": frame_rate or source.get("frame_rate"),
            "media_mode": source["media_mode"],
            "audio_path": str(audio_path),
            "visual_path": str(visual_path),
            "visual_width": (
                visual_geometry.width if visual_geometry is not None else source.get("width")
            ),
            "visual_height": (
                visual_geometry.height if visual_geometry is not None else source.get("height")
            ),
            "audio_width": (
                audio_geometry.width if audio_geometry is not None else source.get("audio_width")
            ),
            "audio_height": (
                audio_geometry.height if audio_geometry is not None else source.get("audio_height")
            ),
            "wide_layout": wide_layout.to_dict() if wide_layout is not None else None,
        }

    def _transcribe(
        self, source: dict[str, Any], project: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        source_workspace = self.options.workspace / source["source_id"]
        source_workspace.mkdir(parents=True, exist_ok=True)
        output = source_workspace / "transcript.exo"
        effective_config = self._subtitle_cleanup_model_config()
        effective_config_path = source_workspace / "transcription-config.json"
        effective_config_path.write_text(
            json.dumps(effective_config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        args = [
            sys.executable,
            str(self.options.pipeline_script),
            source["audio_path"],
            "--workflow",
            "hosted-long-stream",
            "--config",
            str(effective_config_path),
            "--env-file",
            str(self.options.env_file),
            "--output",
            str(output),
            "--sidecar-dir",
            str(source_workspace),
            "--audio-track",
            str(self.options.audio_track),
            "--profile",
        ]
        if self.options.glossary_path is not None:
            args.extend(["--glossary", str(self.options.glossary_path)])
        else:
            args.append("--no-glossary")
        process = subprocess.Popen(
            args,
            cwd=self.options.pipeline_script.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
        code = process.wait()
        if code != 0:
            raise SubtitlerError(
                f"Transcription pipeline failed for {source['original_name']} with exit code {code}"
            )
        timing = source_workspace / "transcript.subtitle_timing.csv"
        text = source_workspace / "transcript.final_text.txt"
        if not timing.is_file() or not text.is_file():
            raise SubtitlerError(
                f"Transcription finished without resumable timing artifacts for {source['original_name']}"
            )
        run_metadata_path = source_workspace / "transcript.run.json"
        failed_groups = _failed_transcription_groups(run_metadata_path)
        if failed_groups:
            ranges = _failed_group_ranges(source_workspace / "transcript.vad_groups.csv", failed_groups)
            detail = ", ".join(ranges[:5])
            suffix = "…" if len(ranges) > 5 else ""
            raise SubtitlerError(
                f"Transcription left {len(failed_groups)} audio group(s) unresolved for "
                f"{source['original_name']}: {detail}{suffix}. Resume from transcription; "
                "semantic analysis was not allowed to use an incomplete transcript."
            )
        transcript = _load_transcript_evidence(timing, text)
        aligned_tokens = source_workspace / "transcript.aligned_tokens.csv"
        api_usage_path = source_workspace / "transcript.api_usage.csv"
        return {
            "exo_path": str(output),
            "timing_path": str(timing),
            "text_path": str(text),
            "aligned_tokens_path": str(aligned_tokens) if aligned_tokens.is_file() else None,
            "subtitle_mode": "full",
            "speech_segments": len(transcript),
            "first_speech_ms": transcript[0].start_ms if transcript else None,
            "last_speech_ms": transcript[-1].end_ms if transcript else None,
            "api_cost_usd": _sum_api_usage_cost(api_usage_path),
            "api_usage_path": str(api_usage_path),
        }

    def _analyze_visuals(
        self,
        source: dict[str, Any],
        project: dict[str, Any],
        prior_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        probe = prior_outputs.get("source_probe")
        if not isinstance(probe, dict):
            raise SubtitlerError("Visual analysis requires the completed source probe")
        editorial_config = self.config.get("editorial", {})
        model = self._editorial_model()
        detail = str(editorial_config.get("visual_detail") or "detailed")
        if detail not in {"simple", "medium", "detailed", "precise", "probe"}:
            detail = "detailed"
        existing_profile = load_game_profile(
            self.options.game_knowledge_path,
            str(project["title_or_game"]),
        )
        reference_context = (
            existing_profile.get("reference_context")
            if isinstance(existing_profile.get("reference_context"), dict)
            else {}
        )
        if reference_context.get("status") != "complete":
            print(
                _message(
                    project,
                    "Visual learning: consulting bounded public game reference...",
                    "映像学習: 範囲を限定して公開ゲーム情報を確認中…",
                ),
                flush=True,
            )
            reference_context = lookup_game_wiki(str(project["title_or_game"]))
        contextual_profile = dict(existing_profile)
        contextual_profile["reference_context"] = reference_context
        print(
            _message(
                project,
                "Visual learning: sampling gameplay and scanning voice energy in parallel...",
                "映像学習: ゲーム映像のサンプリングと音声強度の解析を並列実行中…",
            ),
            flush=True,
        )
        provider_context = (
            f"Game/title: {project['title_or_game']}. Objective: {project['objective']}. "
            f"Known reusable game cues: {game_profile_context(contextual_profile)}"
        )
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="editorial-evidence") as pool:
            visual_future = pool.submit(
                _analyze_editorial_visual_windows,
                media_path=Path(source["visual_path"]),
                duration_sec=float(probe["duration_ms"]) / 1000.0,
                detail=detail,
                ffmpeg="ffmpeg",
                sampling_scale=float(editorial_config.get("visual_sampling_scale") or 1.5),
                model=model,
                reasoning_effort=self._visual_reasoning_effort(),
                output_locale=str(project.get("output_locale", "en")),
                editorial_context=provider_context,
                progress_path=(
                    self.options.workspace / source["source_id"] / "visual.window_progress.json"
                ),
                diagnostics_path=(
                    self.options.workspace / source["source_id"] / "visual.structured_responses.jsonl"
                ),
                progress=lambda complete, total, ranges: print(
                    _message(
                        project,
                        f"Visual learning: state window {complete}/{total} complete "
                        f"({ranges} event range(s) so far)...",
                        f"映像学習: 状態ウィンドウ {complete}/{total} が完了 "
                        f"（現在 {ranges} イベント区間）…",
                    ),
                    flush=True,
                ),
            )
            acoustic_future = pool.submit(
                analyze_acoustic_emphasis,
                Path(source["audio_path"]),
                duration_ms=int(probe["duration_ms"]),
                audio_track=self.options.audio_track,
            )
            result = visual_future.result()
            acoustic_events = acoustic_future.result()
        output = asdict(result)
        bursts = {
            "bursts": [],
            "cost_usd": 0.0,
            "prompt_version": "disabled-for-cutting-assistant",
        }
        print(
            _message(
                project,
                f"Visual learning: dense map complete ({len(output.get('segments', []))} visual range(s), "
                f"{len(acoustic_events)} acoustic cue(s)); updating reusable game knowledge...",
                f"映像学習: 詳細マップが完了（映像区間 {len(output.get('segments', []))} 件、"
                f"音響キュー {len(acoustic_events)} 件）。再利用可能なゲーム知識を更新中…",
            ),
            flush=True,
        )
        transcription = prior_outputs.get("transcription")
        transcript_excerpt: list[dict[str, Any]] = []
        if isinstance(transcription, dict):
            evidence = _load_transcript_evidence(
                Path(transcription["timing_path"]),
                Path(transcription["text_path"]),
            )
            transcript_excerpt = [asdict(item) for item in _representative_transcript(evidence)]
        usage = ApiUsageLedger()
        refiner = self._build_game_learning_refiner(
            usage, self.options.workspace / source["source_id"] / "game-learning"
        )
        if refiner is None or not hasattr(refiner, "complete_structured"):
            raise SubtitlerError("Hosted game learning requires a structured cleanup model")
        try:
            try:
                profile = update_game_profile(
                    path=self.options.game_knowledge_path,
                    title=str(project["title_or_game"]),
                    provider=refiner,
                    visual_summary={
                        "description": output.get("description", ""),
                        "tags": output.get("tags", []),
                        "segments": output.get("segments", []),
                    },
                    transcript_excerpt=transcript_excerpt,
                    temporal_bursts=bursts.get("bursts", []),
                    reference_context=reference_context,
                    output_locale=str(project.get("output_locale", "en")),
                )
            except Exception as exc:
                print(
                    _message(
                        project,
                        f"Warning: persistent game learning failed; using prior knowledge: {exc}",
                        f"警告: ゲーム知識の保存に失敗しました。以前の知識を使用します: {exc}",
                    ),
                    flush=True,
                )
                profile = existing_profile
        finally:
            refiner.close()
        output["acoustic_events"] = acoustic_events
        output["acoustic_analysis"] = {
            "status": getattr(acoustic_events, "status", "complete"),
            "detail": getattr(acoustic_events, "detail", ""),
            "event_count": len(acoustic_events),
        }
        output["temporal_bursts"] = bursts.get("bursts", [])
        output["temporal_burst_prompt_version"] = bursts.get("prompt_version")
        output["game_knowledge"] = profile
        output["api_cost_usd"] = (
            float(output.get("cost_usd", 0.0))
            + float(bursts.get("cost_usd", 0.0))
            + usage.total_cost_usd
        )
        output["api_usage"] = [row.__dict__ for row in usage.rows]
        print(
            _message(
                project,
                f"Visual learning: complete; game profile revision {profile.get('revision', 0)}.",
                f"映像学習: 完了。ゲームプロファイル改訂 {profile.get('revision', 0)}。",
            ),
            flush=True,
        )
        return output

    def _analyze_semantics(
        self,
        source: dict[str, Any],
        project: dict[str, Any],
        prior_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        transcription = prior_outputs.get("transcription")
        visual = prior_outputs.get("visual_learning")
        probe = prior_outputs.get("source_probe")
        if not isinstance(transcription, dict) or not isinstance(visual, dict) or not isinstance(probe, dict):
            raise SubtitlerError("Semantic analysis requires completed transcript, vision, and probe stages")
        transcript = _load_transcript_evidence(
            Path(transcription["timing_path"]),
            Path(transcription["text_path"]),
        )
        transcript = _tighten_transcript_to_speech_activity(
            transcript,
            _load_vad_speech_activity(
                Path(transcription["timing_path"]).with_name(
                    "transcript.vad_selection.csv"
                )
            ),
        )
        visuals = [
            VisualEvidence(
                start_ms=int(item.get("start_ms", 0)),
                end_ms=int(item.get("end_ms", 0)),
                description=str(item.get("description") or ""),
                tags=tuple(str(tag) for tag in item.get("tags", []) if str(tag).strip()),
                confidence=float(item.get("confidence", 0.0)),
                motion_level=(float(item["motion_level"]) if item.get("motion_level") is not None else None),
                visual_category=str(item.get("visual_category") or "other"),
                observed_label=str(item.get("observed_label") or ""),
            )
            for item in visual.get("segments", [])
            if isinstance(item, dict) and int(item.get("end_ms", 0)) > int(item.get("start_ms", 0))
        ]
        usage = ApiUsageLedger()
        refiner = self._build_editorial_refiner(
            usage, self.options.workspace / source["source_id"] / "editorial"
        )
        if refiner is None or not hasattr(refiner, "complete_structured"):
            raise SubtitlerError("Hosted editorial analysis requires a structured cleanup model")
        source_workspace = self.options.workspace / source["source_id"]
        semantic_progress_path = source_workspace / "editorial.semantic_progress.json"
        diagnostics_path = source_workspace / "editorial.structured_responses.jsonl"
        semantic_progress = _load_semantic_progress(
            semantic_progress_path,
            source_id=str(source["source_id"]),
            source_duration_ms=int(probe["duration_ms"]),
        )
        semantic_progress_lock = threading.Lock()

        def record_completed_window(window: dict[str, Any]) -> None:
            with semantic_progress_lock:
                completed = semantic_progress["completed_windows"]
                base_index = int(window["base_window_index"])
                completed[:] = [
                    item for item in completed if int(item.get("base_window_index", -1)) != base_index
                ]
                completed.append(window)
                completed.sort(key=lambda item: int(item.get("base_window_index", -1)))
                _write_json_atomic(semantic_progress_path, semantic_progress)

        try:
            result = analyze_editorial_source(
                provider=refiner,
                source_id=source["source_id"],
                source_duration_ms=int(probe["duration_ms"]),
                title_or_game=project["title_or_game"],
                objective=project["objective"],
                transcript=transcript,
                visuals=visuals,
                cumulative_context=project["cumulative_context"],
                acoustic_events=visual.get("acoustic_events", []),
                temporal_bursts=visual.get("temporal_bursts", []),
                game_knowledge=game_profile_context(
                    visual.get("game_knowledge", {})
                    if isinstance(visual.get("game_knowledge"), dict)
                    else {}
                ),
                progress=lambda message: print(
                    _message(
                        project,
                        f"Editorial analysis: {message}",
                        f"編集分析: {message}",
                    ),
                    flush=True,
                ),
                completed_windows=semantic_progress["completed_windows"],
                window_completed=record_completed_window,
                output_locale=str(project.get("output_locale", "en")),
            )
            print(
                _message(
                    project,
                    "Editorial analysis: factual event mapping complete.",
                    "編集分析: 事実ベースのイベント整理が完了しました。",
                ),
                flush=True,
            )
        except Exception as exc:
            failure_output = {
                "api_cost_usd": usage.total_cost_usd,
                "api_usage": [row.__dict__ for row in usage.rows],
                "structured_response_diagnostics_path": str(diagnostics_path),
                "semantic_progress_path": str(semantic_progress_path),
            }
            setattr(exc, "editorial_failure_output", failure_output)
            raise
        finally:
            refiner.close()
        result["api_cost_usd"] = usage.total_cost_usd
        result["api_usage"] = [row.__dict__ for row in usage.rows]
        result["structured_response_diagnostics_path"] = str(diagnostics_path)
        result["semantic_progress_path"] = str(semantic_progress_path)
        return result

    @staticmethod
    def _reconcile(prior_outputs: dict[str, Any]) -> dict[str, Any]:
        semantics = prior_outputs.get("semantic_spans")
        if not isinstance(semantics, dict):
            raise SubtitlerError("Local reconciliation requires completed semantic analysis")
        coverage = [dict(item) for item in semantics.get("timeline_coverage", []) if isinstance(item, dict)]
        for item in coverage:
            item["source_id"] = semantics.get("source_id", "")
        return {
            "global_threads": [],
            "recommendations": semantics.get("recommendations", []),
            "narration_briefs": semantics.get("narration_briefs", []),
            "creative_suggestions": semantics.get("creative_suggestions", []),
            "timeline_coverage": coverage,
            "connections": semantics.get("connections", []),
            "conflicts": [],
            "semantic_spans": semantics.get("semantic_spans", []),
            "audio_intent_spans": semantics.get("audio_intent_spans", []),
            "safe_boundaries_ms": semantics.get("safe_boundaries_ms", []),
            "speech_segments": semantics.get("speech_segments", []),
            "utterance_groups": semantics.get("utterance_groups", []),
            "event_graph": semantics.get("event_graph", {"nodes": [], "edges": []}),
            "activity_episodes": semantics.get("activity_episodes", []),
        }


def _analyze_editorial_visual_windows(
    *,
    media_path: Path,
    duration_sec: float,
    detail: str,
    ffmpeg: str,
    sampling_scale: float,
    model: str,
    reasoning_effort: str,
    output_locale: str,
    editorial_context: str,
    progress_path: Path | None = None,
    diagnostics_path: Path | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    max_workers: int | None = None,
    window_interval_sec: float = 0.0,
) -> MediaAnalysisResult:
    """Build a dense event timeline in bounded requests instead of one giant image call."""
    windows = []
    start_sec = 0.0
    while start_sec < duration_sec:
        end_sec = min(duration_sec, start_sec + EDITORIAL_VISUAL_WINDOW_SECONDS)
        windows.append((start_sec, end_sec))
        start_sec = end_sec
    if not windows:
        windows = [(0.0, max(0.1, duration_sec))]

    signature = {
        "visual_stage_version": EDITORIAL_STAGE_VERSIONS["visual_learning"],
        "duration_sec": duration_sec,
        "detail": detail,
        "sampling_scale": sampling_scale,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "window_seconds": EDITORIAL_VISUAL_WINDOW_SECONDS,
    }
    cached = _load_visual_window_progress(progress_path, signature)
    progress_lock = threading.Lock()
    request_lock = threading.Lock()
    last_window_started = [0.0]

    def cached_result(start: float, end: float) -> MediaAnalysisResult | None:
        with progress_lock:
            value = cached.get(_visual_window_key(start, end))
        return _media_analysis_result_from_dict(value) if isinstance(value, dict) else None

    def persist_result(start: float, end: float, result: MediaAnalysisResult) -> None:
        if progress_path is None:
            return
        with progress_lock:
            cached[_visual_window_key(start, end)] = asdict(result)
            _write_json_atomic(
                progress_path,
                {**signature, "completed_windows": cached},
            )

    def analyze_range(start: float, end: float, split_depth: int = 0) -> MediaAnalysisResult:
        restored = cached_result(start, end)
        if restored is not None:
            return restored
        provider = OpenAIEditorialVisualProvider(
            model,
            output_locale=output_locale,
            editorial_context=editorial_context,
            reasoning_effort=reasoning_effort,
            diagnostics_path=diagnostics_path,
        )
        try:
            result = analyze_media(
                media_path=media_path,
                media_kind="video",
                duration_sec=duration_sec,
                detail=detail,
                ffmpeg=ffmpeg,
                provider=provider,
                start_sec=start,
                end_sec=end,
                sampling_scale=sampling_scale,
                max_ranges=min(64, max(12, round((end - start) / 20.0))),
                include_frame_differences=False,
            )
        except MediaAnalysisResponseError:
            if split_depth >= MAX_EDITORIAL_VISUAL_SPLIT_DEPTH or end - start < 4 * 60.0:
                raise
            midpoint = start + (end - start) / 2.0
            print(
                locale_label(
                    output_locale,
                    f"Visual learning: retrying {_visual_clock(start)}-{_visual_clock(end)} "
                    "as two smaller structured requests...",
                    f"映像学習: {_visual_clock(start)}-{_visual_clock(end)} を、"
                    "2 件の小さな構造化リクエストに分けて再試行します…",
                ),
                flush=True,
            )
            result = _merge_visual_results(
                [
                    analyze_range(start, midpoint, split_depth + 1),
                    analyze_range(midpoint, end, split_depth + 1),
                ],
                prompt_suffix=f"split-recovery-{split_depth + 1}",
            )
        persist_result(start, end, result)
        return result

    def analyze_window(bounds: tuple[float, float]) -> MediaAnalysisResult:
        restored = cached_result(*bounds)
        if restored is not None:
            return restored
        if window_interval_sec > 0:
            with request_lock:
                remaining = float(window_interval_sec) - (time.monotonic() - last_window_started[0])
                if remaining > 0:
                    time.sleep(remaining)
                last_window_started[0] = time.monotonic()
        return analyze_range(*bounds)

    results: dict[int, MediaAnalysisResult] = {}
    requested_workers = MAX_EDITORIAL_VISUAL_WORKERS if max_workers is None else max(1, int(max_workers))
    workers = min(requested_workers, MAX_EDITORIAL_VISUAL_WORKERS, len(windows))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="editorial-visual-state")
    futures: dict[Any, int] = {}
    next_index = 0
    completed_ranges = 0
    try:
        while next_index < min(workers, len(windows)):
            futures[pool.submit(analyze_window, windows[next_index])] = next_index
            next_index += 1
        while futures:
            future = next(as_completed(futures))
            index = futures.pop(future)
            result = future.result()
            results[index] = result
            completed_ranges += len(result.segments)
            if progress is not None:
                progress(len(results), len(windows), completed_ranges)
            if next_index < len(windows):
                futures[pool.submit(analyze_window, windows[next_index])] = next_index
                next_index += 1
    except BaseException:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)

    ordered = [results[index] for index in range(len(windows))]
    described = [
        MediaAnalysisResult(
            description=(
                f"{_visual_clock(windows[index][0])}-{_visual_clock(windows[index][1])}: "
                f"{result.description}"
            ),
            tags=result.tags,
            segments=result.segments,
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            sample_count=result.sample_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            frame_differences=result.frame_differences,
        )
        for index, result in enumerate(ordered)
    ]
    return _merge_visual_results(described, prompt_suffix="windowed-state-v2")


def _visual_window_key(start_sec: float, end_sec: float) -> str:
    return f"{start_sec:.3f}-{end_sec:.3f}"


def _load_visual_window_progress(
    path: Path | None, signature: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in signature.items()):
        return {}
    completed = value.get("completed_windows")
    if not isinstance(completed, dict):
        return {}
    return {
        str(key): item
        for key, item in completed.items()
        if isinstance(item, dict)
    }


def _media_analysis_result_from_dict(value: dict[str, Any]) -> MediaAnalysisResult | None:
    try:
        segments = [
            AnalysisSegment(
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                description=str(item.get("description") or ""),
                tags=[str(tag) for tag in item.get("tags", [])],
                confidence=float(item.get("confidence", 0.0)),
                motion_level=(
                    float(item["motion_level"])
                    if item.get("motion_level") is not None
                    else None
                ),
                visual_category=str(item.get("visual_category") or "other"),
                suitability=str(item.get("suitability") or ""),
                handoff_required=bool(item.get("handoff_required")),
                handoff_reason=str(item.get("handoff_reason") or ""),
            )
            for item in value.get("segments", [])
            if isinstance(item, dict)
        ]
        return MediaAnalysisResult(
            description=str(value["description"]),
            tags=[str(tag) for tag in value.get("tags", [])],
            segments=segments,
            provider=str(value["provider"]),
            model=str(value["model"]),
            prompt_version=str(value["prompt_version"]),
            sample_count=int(value.get("sample_count", 0)),
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            cost_usd=float(value.get("cost_usd", 0.0)),
            frame_differences=[
                dict(item) for item in value.get("frame_differences", []) if isinstance(item, dict)
            ],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _merge_visual_results(
    values: list[MediaAnalysisResult], *, prompt_suffix: str
) -> MediaAnalysisResult:
    if not values:
        raise SubtitlerError("Visual analysis produced no completed windows")
    tags = list(
        dict.fromkeys(
            tag for result in values for tag in result.tags if str(tag).strip()
        )
    )
    return MediaAnalysisResult(
        description="\n".join(
            result.description for result in values if result.description.strip()
        )[:48_000],
        tags=tags[:120],
        segments=[segment for result in values for segment in result.segments],
        provider=values[0].provider,
        model=values[0].model,
        prompt_version=f"{values[0].prompt_version}-{prompt_suffix}",
        sample_count=sum(result.sample_count for result in values),
        input_tokens=sum(result.input_tokens for result in values),
        output_tokens=sum(result.output_tokens for result in values),
        cost_usd=sum(result.cost_usd for result in values),
        frame_differences=[
            difference for result in values for difference in result.frame_differences
        ],
    )


def _visual_clock(seconds: float) -> str:
    total = max(0, round(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _message(project: dict[str, Any], english: str, japanese: str) -> str:
    return locale_label(project.get("output_locale"), english, japanese)


def _load_transcript_evidence(timing_path: Path, text_path: Path) -> list[TranscriptEvidence]:
    try:
        numbered_text = text_path.read_text(encoding="utf-8").splitlines()
        texts = []
        for line in numbered_text:
            _, separator, text = line.partition(". ")
            texts.append(text if separator else line)
        with timing_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SubtitlerError(f"Could not load transcript evidence: {exc}") from exc
    if len(rows) != len(texts):
        raise SubtitlerError(
            f"Transcript timing/text count mismatch: {len(rows)} timing rows, {len(texts)} text rows"
        )
    result = []
    for row, text in zip(rows, texts):
        try:
            start_ms = round(float(row["start"]) * 1000)
            end_ms = round(float(row["end"]) * 1000)
        except (KeyError, TypeError, ValueError) as exc:
            raise SubtitlerError("Transcript timing artifact contains an invalid row") from exc
        if text.strip() and end_ms > start_ms:
            result.append(TranscriptEvidence(start_ms, end_ms, text.strip()))
    return result


def _load_vad_speech_activity(path: Path) -> list[tuple[int, int]]:
    """Load fine VAD regions used by transcription as acoustic speech evidence."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (FileNotFoundError, OSError, UnicodeError, csv.Error):
        return []
    result: list[tuple[int, int]] = []
    for row in rows:
        if str(row.get("selected_for_transcription") or "").strip().casefold() not in {
            "1",
            "true",
            "yes",
        }:
            continue
        try:
            start_ms = round(float(row["start"]) * 1000)
            end_ms = round(float(row["end"]) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        if end_ms > start_ms:
            result.append((start_ms, end_ms))
    return sorted(result)


def _tighten_transcript_to_speech_activity(
    transcript: list[TranscriptEvidence],
    speech_activity: list[tuple[int, int]],
) -> list[TranscriptEvidence]:
    """Remove acoustic silence stretched into the outside of aligned text ranges."""
    if not speech_activity:
        return transcript
    tightened: list[TranscriptEvidence] = []
    for item in transcript:
        start_ms, end_ms = _tighten_range_to_speech_activity(
            item.start_ms,
            item.end_ms,
            speech_activity,
        )
        tightened.append(TranscriptEvidence(start_ms, end_ms, item.text))

    # Older cached transcription artifacts can contain a punctuation-only row
    # forced-aligned after several seconds of silence. Preserve its text while
    # attaching it to an adjacent spoken row instead of extending the range.
    result: list[TranscriptEvidence] = []
    pending_leading = ""
    for index, item in enumerate(tightened):
        acoustically_supported = any(
            speech_start < item.end_ms and speech_end > item.start_ms
            for speech_start, speech_end in speech_activity
        )
        if not _is_non_spoken_text(item.text) or acoustically_supported:
            text = f"{pending_leading}{item.text}" if pending_leading else item.text
            pending_leading = ""
            result.append(TranscriptEvidence(item.start_ms, item.end_ms, text))
            continue
        if result and not _is_non_spoken_text(result[-1].text):
            previous = result[-1]
            result[-1] = TranscriptEvidence(
                previous.start_ms,
                previous.end_ms,
                f"{previous.text}{item.text}",
            )
            continue
        if any(not _is_non_spoken_text(candidate.text) for candidate in tightened[index + 1 :]):
            pending_leading = f"{pending_leading}{item.text}"
        else:
            result.append(item)
    return result


def _tighten_range_to_speech_activity(
    start_ms: int,
    end_ms: int,
    speech_activity: list[tuple[int, int]],
) -> tuple[int, int]:
    overlapping = [
        (speech_start, speech_end)
        for speech_start, speech_end in speech_activity
        if speech_start < end_ms and speech_end > start_ms
    ]
    if not overlapping:
        return start_ms, end_ms
    tightened_start = max(start_ms, overlapping[0][0])
    tightened_end = min(end_ms, overlapping[-1][1])
    if tightened_end <= tightened_start:
        return start_ms, end_ms
    return tightened_start, tightened_end


def _align_emphasized_phrases(
    phrases: Any,
    aligned_tokens_path: Path | None,
) -> list[dict[str, Any]]:
    """Verify verbatim phrase evidence and replace broad model ranges with token timing."""
    if not isinstance(phrases, list) or aligned_tokens_path is None or not aligned_tokens_path.is_file():
        return []
    try:
        with aligned_tokens_path.open("r", encoding="utf-8", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if str(row.get("text") or "")]
    except (OSError, UnicodeError, csv.Error):
        return []
    speech_activity = _load_vad_speech_activity(
        aligned_tokens_path.with_name("transcript.vad_selection.csv")
    )
    stream_chars: list[str] = []
    char_tokens: list[int] = []
    for token_index, row in enumerate(rows):
        for character in str(row.get("text") or "").casefold():
            if character.isspace():
                continue
            stream_chars.append(character)
            char_tokens.append(token_index)
    stream = "".join(stream_chars)
    result: list[dict[str, Any]] = []
    for item in phrases:
        if not isinstance(item, dict):
            continue
        source_text = str(item.get("source_text") or "")
        needle, source_positions = _normalized_character_positions(source_text)
        if not needle:
            continue
        candidates: list[int] = []
        start = stream.find(needle)
        while start >= 0:
            candidates.append(start)
            start = stream.find(needle, start + 1)
        if not candidates:
            continue
        proposed_mid = (int(item.get("start_ms", 0)) + int(item.get("end_ms", 0))) // 2
        def distance(position: int) -> float:
            token = rows[char_tokens[position]]
            try:
                return abs(float(token["start"]) * 1000 - proposed_mid)
            except (KeyError, TypeError, ValueError):
                return float("inf")
        match = min(candidates, key=distance)
        aligned_segments: list[dict[str, Any]] = []
        for character_start, character_end in _editorial_subtitle_character_ranges(
            needle,
            match=match,
            char_tokens=char_tokens,
        ):
            first_index = char_tokens[match + character_start]
            last_index = char_tokens[match + character_end - 1]
            timing = _bounded_emphasis_timing(rows, first_index, last_index)
            if timing is None:
                continue
            start_ms, end_ms = _tighten_range_to_speech_activity(
                timing[0],
                timing[1],
                speech_activity,
            )
            if end_ms <= start_ms:
                continue
            chunk_text = _source_text_character_slice(
                source_text,
                source_positions,
                character_start,
                character_end,
            )
            if not chunk_text:
                continue
            normalized = dict(item)
            normalized.update(
                {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "timing_verified": True,
                    "source_text": chunk_text,
                    "text": chunk_text,
                    "parent_source_text": source_text,
                }
            )
            aligned_segments.append(normalized)
        segment_count = len(aligned_segments)
        for segment_index, normalized in enumerate(aligned_segments, 1):
            normalized["display_segment_index"] = segment_index
            normalized["display_segment_count"] = segment_count
            if normalized.get("id") and segment_count > 1:
                normalized["id"] = f"{normalized['id']}-{segment_index}"
            result.append(normalized)
    return _deduplicate_emphasized_phrases(result)


def _normalized_character_positions(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    source_positions: list[int] = []
    for source_index, character in enumerate(value):
        for normalized in character.casefold():
            if normalized.isspace():
                continue
            characters.append(normalized)
            source_positions.append(source_index)
    return "".join(characters), source_positions


def _editorial_subtitle_character_ranges(
    value: str,
    *,
    match: int,
    char_tokens: list[int],
) -> list[tuple[int, int]]:
    """Split one selected thought into short, token-timed, single-line beats."""
    length = len(value)
    if length <= EDITORIAL_SUBTITLE_TARGET_CHARS:
        return [(0, length)] if length else []
    strong_breaks = set(".!?。！？")
    soft_breaks = set(",、，:：;；…—-")
    token_boundaries = {
        index
        for index in range(1, length)
        if char_tokens[match + index - 1] != char_tokens[match + index]
    }
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while length - cursor > EDITORIAL_SUBTITLE_TARGET_CHARS:
        target = min(length, cursor + EDITORIAL_SUBTITLE_TARGET_CHARS)
        maximum = min(length, cursor + EDITORIAL_SUBTITLE_MAX_CHARS)
        minimum = min(length, cursor + EDITORIAL_SUBTITLE_MIN_CHARS)
        semantic_before = [
            index
            for index in range(minimum, target + 1)
            if value[index - 1] in strong_breaks | soft_breaks
        ]
        boundaries_before = [
            index
            for index in token_boundaries
            if minimum <= index <= target
        ]
        semantic_after = [
            index
            for index in range(target + 1, maximum + 1)
            if value[index - 1] in strong_breaks | soft_breaks
        ]
        boundaries_after = [
            index
            for index in token_boundaries
            if target < index <= maximum
        ]
        if semantic_before:
            end = semantic_before[-1]
        elif boundaries_before:
            end = boundaries_before[-1]
        elif semantic_after:
            end = semantic_after[0]
        elif boundaries_after:
            end = boundaries_after[0]
        else:
            end = target
        if length - end < EDITORIAL_SUBTITLE_MIN_CHARS and length - cursor <= EDITORIAL_SUBTITLE_MAX_CHARS:
            end = length
        if end <= cursor:
            end = min(length, cursor + EDITORIAL_SUBTITLE_TARGET_CHARS)
        ranges.append((cursor, end))
        cursor = end
    if cursor < length:
        ranges.append((cursor, length))
    return ranges


def _source_text_character_slice(
    value: str,
    source_positions: list[int],
    start: int,
    end: int,
) -> str:
    if not source_positions or start >= end:
        return ""
    raw_start = source_positions[start]
    raw_end = source_positions[end] if end < len(source_positions) else len(value)
    return " ".join(value[raw_start:raw_end].split())


def _clean_selected_editorial_subtitles(
    phrases: list[dict[str, Any]],
    refiner: Any,
    *,
    batch_size: int = 64,
) -> list[dict[str, Any]]:
    """Clean only the verified phrases selected for the final editorial track."""
    cleaned_phrases: list[dict[str, Any]] = []
    for start in range(0, len(phrases), max(1, batch_size)):
        batch = phrases[start : start + max(1, batch_size)]
        originals = [str(item.get("text") or "").strip() for item in batch]
        refined = refiner.refine(originals)
        if not isinstance(refined, list) or len(refined) != len(originals):
            refined = originals
        for item, original, cleaned in zip(batch, originals, refined):
            normalized = dict(item)
            cleaned_text = " ".join(str(cleaned).split()) or original
            if len("".join(cleaned_text.split())) > EDITORIAL_SUBTITLE_MAX_CHARS:
                cleaned_text = original
            normalized["text"] = cleaned_text
            normalized["cleanup_applied"] = True
            cleaned_phrases.append(normalized)
    return cleaned_phrases


def _bounded_emphasis_timing(
    rows: list[dict[str, str]], first_index: int, last_index: int
) -> tuple[int, int] | None:
    """Keep trustworthy token timing while rejecting silence-stretched phrases."""
    selected = rows[first_index:last_index + 1]
    parsed: list[tuple[float, float, str]] = []
    try:
        for row in selected:
            start = float(row["start"])
            end = float(row["end"])
            text = str(row.get("text") or "")
            if end < start:
                return None
            parsed.append((start, end, text))
    except (KeyError, TypeError, ValueError):
        return None
    spoken = [
        index
        for index, (_, _, text) in enumerate(parsed)
        if any(not _is_punctuation(character) for character in text if not character.isspace())
    ]
    if not spoken:
        return None
    first_spoken = spoken[0]
    last_spoken = spoken[-1]
    for index in spoken[1:-1]:
        start, end, text = parsed[index]
        if end - start > _emphasis_token_limit(text, internal=True):
            return None
    for left, right in zip(spoken, spoken[1:]):
        if parsed[right][0] - parsed[left][1] > 1.25:
            return None
    first_start, first_end, first_text = parsed[first_spoken]
    last_start, last_end, last_text = parsed[last_spoken]
    start = max(first_start, first_end - _emphasis_token_limit(first_text, internal=False))
    end = min(last_end, last_start + _emphasis_token_limit(last_text, internal=False))
    if end <= start:
        return None
    return round(start * 1000), round(end * 1000)


def _emphasis_token_limit(text: str, *, internal: bool) -> float:
    visible = sum(not character.isspace() and not _is_punctuation(character) for character in text)
    per_character = 0.45 if internal else 0.22
    floor = 1.5 if internal else 0.75
    return max(floor, visible * per_character)


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith(("P", "S"))


def _is_non_spoken_text(text: str) -> bool:
    visible = [character for character in text if not character.isspace()]
    return bool(visible) and all(_is_punctuation(character) for character in visible)


def _deduplicate_emphasized_phrases(
    phrases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in sorted(
        phrases,
        key=lambda value: (
            int(value.get("start_ms", 0)),
            int(value.get("end_ms", 0)),
            -len(str(value.get("text") or "")),
        ),
    ):
        key = _emphasis_text_key(item.get("text"))
        if not key:
            continue
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(selected)
                if abs(int(existing.get("start_ms", 0)) - int(item.get("start_ms", 0))) <= 1500
                and _emphasis_texts_overlap(key, _emphasis_text_key(existing.get("text")))
            ),
            None,
        )
        if duplicate_index is None:
            selected.append(item)
            continue
        existing = selected[duplicate_index]
        if _emphasis_preference(item) > _emphasis_preference(existing):
            selected[duplicate_index] = item
    return sorted(selected, key=lambda value: (int(value["start_ms"]), int(value["end_ms"])))


def _emphasis_text_key(value: Any) -> str:
    return "".join(
        character.casefold()
        for character in str(value or "")
        if not character.isspace() and not _is_punctuation(character)
    )


def _emphasis_texts_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    shorter, longer = sorted((left, right), key=len)
    return shorter == longer or (len(shorter) >= 6 and shorter in longer)


def _emphasis_preference(item: dict[str, Any]) -> tuple[int, float, int]:
    text = str(item.get("text") or "")
    return (
        len(_emphasis_text_key(text)),
        float(item.get("confidence") or 0.0),
        len(text),
    )


def _representative_transcript(
    transcript: list[TranscriptEvidence], *, limit: int = 120
) -> list[TranscriptEvidence]:
    """Keep a bounded chronological sample while retaining the beginning and end."""
    if len(transcript) <= limit:
        return transcript
    indices = {
        round(index * (len(transcript) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [transcript[index] for index in sorted(indices)]


def _failed_transcription_groups(run_metadata_path: Path) -> list[int]:
    try:
        payload = json.loads(run_metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubtitlerError(f"Could not inspect transcription completion metadata: {exc}") from exc
    diagnostics = payload.get("backend", {}).get("diagnostics", []) if isinstance(payload, dict) else []
    result = []
    for item in diagnostics if isinstance(diagnostics, list) else []:
        if not isinstance(item, dict) or item.get("code") != "transcription_failed":
            continue
        index = item.get("region_index")
        if isinstance(index, int) and not isinstance(index, bool):
            result.append(index)
    return sorted(set(result))


def _failed_group_ranges(path: Path, failed_groups: list[int]) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error):
        return [f"group {index}" for index in failed_groups]
    by_index = {str(row.get("chunk_index")): row for row in rows}
    result = []
    for index in failed_groups:
        row = by_index.get(str(index))
        if row is None:
            result.append(f"group {index}")
            continue
        try:
            start = float(row["start"])
            end = float(row["end"])
            result.append(f"{start / 60:.1f}-{end / 60:.1f} min")
        except (KeyError, TypeError, ValueError):
            result.append(f"group {index}")
    return result


def _probe_frame_rate(path: Path) -> float:
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return 0.0
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        return 0.0
    for field in ("avg_frame_rate", "r_frame_rate"):
        value = _parse_frame_rate(streams[0].get(field))
        if value > 0:
            return value
    return 0.0


def _parse_frame_rate(value: Any) -> float:
    if not isinstance(value, str):
        return 0.0
    numerator, separator, denominator = value.partition("/")
    try:
        result = float(numerator) / float(denominator) if separator else float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return result if result > 0 else 0.0


def _load_semantic_progress(
    path: Path,
    *,
    source_id: str,
    source_duration_ms: int,
) -> dict[str, Any]:
    expected = {
        "transcription_stage_version": EDITORIAL_STAGE_VERSIONS["transcription"],
        "semantic_stage_version": EDITORIAL_STAGE_VERSIONS["semantic_spans"],
        "visual_stage_version": EDITORIAL_STAGE_VERSIONS["visual_learning"],
        "prompt_version": EDITORIAL_PROMPT_VERSION,
        "source_id": source_id,
        "source_duration_ms": source_duration_ms,
    }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        value = None
    if isinstance(value, dict) and all(value.get(key) == expected_value for key, expected_value in expected.items()):
        completed = value.get("completed_windows")
        if isinstance(completed, list):
            return {**expected, "completed_windows": completed}
    return {**expected, "completed_windows": []}




def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sum_api_usage_cost(path: Path) -> float:
    if not path.is_file():
        return 0.0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return sum(float(row.get("cost_usd") or 0.0) for row in csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        raise SubtitlerError(f"Could not read hosted API cost artifact {path}: {exc}") from exc


@contextmanager
def _hosted_progress_updates(
    project: dict[str, Any],
    *,
    english_label: str,
    japanese_label: str,
) -> Iterator[None]:
    """Print honest heartbeats while a non-streaming hosted request is in flight."""
    stopped = threading.Event()
    started = time.monotonic()

    def report() -> None:
        delay = EDITORIAL_PROGRESS_FIRST_UPDATE_SECONDS
        update_index = 0
        while not stopped.wait(delay):
            elapsed = round(time.monotonic() - started)
            if update_index == 0:
                english = (
                    f"{english_label}: the hosted model is still processing "
                    f"({elapsed}s elapsed)..."
                )
                japanese = (
                    f"{japanese_label}: ホストモデルで処理を続けています"
                    f"（{elapsed} 秒経過）…"
                )
            elif update_index == 1:
                english = (
                    f"{english_label}: continuing to review the full project context "
                    f"({elapsed}s elapsed)..."
                )
                japanese = (
                    f"{japanese_label}: プロジェクト全体の情報を引き続き確認中"
                    f"（{elapsed} 秒経過）…"
                )
            else:
                english = f"{english_label}: still processing ({elapsed}s elapsed)..."
                japanese = f"{japanese_label}: 処理を継続中（{elapsed} 秒経過）…"
            print(_message(project, english, japanese), flush=True)
            update_index += 1
            delay = EDITORIAL_PROGRESS_UPDATE_INTERVAL_SECONDS

    reporter = threading.Thread(
        target=report,
        name="editorial-hosted-progress",
        daemon=True,
    )
    reporter.start()
    try:
        yield
    finally:
        stopped.set()
        reporter.join()
