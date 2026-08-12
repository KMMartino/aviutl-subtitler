"""Concrete hosted stages for the checkpointed editorial project runner."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .audio import get_media_duration
from .config import load_workflow_config, validate_workflow_config
from .editorial_analysis import (
    EDITORIAL_PROMPT_VERSION,
    TranscriptEvidence,
    VisualEvidence,
    analyze_editorial_source,
    deduplicate_creative_suggestions,
    reconcile_editorial_project,
    review_editorial_project,
)
from .editorial_project import EDITORIAL_STAGE_VERSIONS
from .editorial_enrichment import (
    analyze_acoustic_emphasis,
    analyze_temporal_bursts,
    apply_targeted_reviews,
    review_editorial_candidates,
)
from .editorial_locale import locale_label
from .env import load_env_file
from .errors import SubtitlerError
from .game_knowledge import (
    game_profile_context,
    load_game_profile,
    update_game_profile,
)
from .game_wiki import lookup_game_wiki
from .media_analysis import OpenAIMediaAnalysisProvider, analyze_media
from .subtitle_stage import build_refiner


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
                refiner = self._build_editorial_refiner(
                    usage, self.options.workspace / "editorial-global"
                )
                if refiner is None or not hasattr(refiner, "complete_structured"):
                    raise SubtitlerError(
                        "Hosted editorial reconciliation requires a structured cleanup model"
                    )
                print(
                    _message(
                        project,
                        "Editorial synthesis: reviewing project-wide threads, pacing, and duration plans...",
                        "編集統合: プロジェクト全体の流れ、テンポ、時間配分を確認中…",
                    ),
                    flush=True,
                )
                try:
                    base_reconciliation = reconcile_editorial_project(
                        provider=refiner, project=project
                    )
                finally:
                    refiner.close()
            else:
                print(
                    _message(
                        project,
                        "Editorial synthesis: reusing completed project synthesis from the failed director attempt.",
                        "編集統合: 前回のディレクター処理前に完了した全体統合を再利用します。",
                    ),
                    flush=True,
                )
            director = self._build_director_refiner(
                usage, self.options.workspace / "editorial-director"
            )
            if director is None or not hasattr(director, "complete_structured"):
                raise SubtitlerError("Hosted final director review requires a structured model")
            print(
                _message(
                    project,
                    "Editorial director: reviewing pacing, intrigue, information density, and continuity...",
                    "編集ディレクター: テンポ、引き、情報密度、連続性を確認中…",
                ),
                flush=True,
            )
            try:
                director_review = review_editorial_project(
                    provider=director,
                    project=project,
                    reconciliation=base_reconciliation,
                )
            finally:
                director.close()
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
                        ),
                        str(
                            self.options.workspace
                            / "editorial-director.structured_responses.jsonl"
                        ),
                    ],
                },
            )
            raise
        result = dict(base_reconciliation)
        result["director_review"] = director_review
        result["director_model"] = self._director_model()
        result["api_cost_usd"] = usage.total_cost_usd
        result["api_usage"] = [row.__dict__ for row in usage.rows]
        print(
            _message(
                project,
                f"Editorial synthesis: complete with {len(result.get('global_threads', []))} thread(s) "
                f"and {len(result.get('conflicts', []))} conflict review(s); final director review complete.",
                f"編集統合: {len(result.get('global_threads', []))} 件の流れと "
                f"{len(result.get('conflicts', []))} 件の競合確認を統合し、最終ディレクター確認が完了しました。",
            ),
            flush=True,
        )
        return result

    def _editorial_model_config(self) -> dict[str, Any]:
        """Keep editorial intelligence independent from subtitle cleanup tuning."""
        config = json.loads(json.dumps(getattr(self, "config", {})))
        editorial = config.get("editorial")
        if not isinstance(editorial, dict):
            editorial = {}
        cleanup = config.setdefault("cleanup", {})
        cleanup["backend"] = "openai"
        cleanup["api_model"] = str(editorial.get("analysis_model") or "gpt-5.6-luna")
        cleanup["reasoning_effort"] = str(editorial.get("reasoning_effort") or "low")
        cleanup["thinking_level"] = None
        return config

    def _editorial_model(self) -> str:
        cleanup = self._editorial_model_config()["cleanup"]
        return str(cleanup["api_model"])

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
            editorial.get("subtitle_cleanup_model") or "gpt-5.4-mini"
        )
        cleanup["reasoning_effort"] = str(
            editorial.get("subtitle_cleanup_reasoning_effort") or "medium"
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
        }

    def _transcribe(
        self, source: dict[str, Any], project: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        source_workspace = self.options.workspace / source["source_id"]
        source_workspace.mkdir(parents=True, exist_ok=True)
        output = source_workspace / "transcript.exo"
        effective_config = self._subtitle_cleanup_model_config()
        effective_config.setdefault("additional_settings", {})["editorial_subtitle_mode"] = (
            (project or {}).get("subtitle_mode", "full")
        )
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
            "subtitle_mode": (project or {}).get("subtitle_mode", "full"),
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
        detail = str(editorial_config.get("visual_detail") or "simple")
        if detail not in {"simple", "medium", "detailed", "precise", "probe"}:
            detail = "simple"
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
        provider = OpenAIMediaAnalysisProvider(
            model,
            output_locale=str(project.get("output_locale", "en")),
            editorial_context=(
                f"Game/title: {project['title_or_game']}. Objective: {project['objective']}. "
                f"Known reusable game cues: {game_profile_context(contextual_profile)}"
            ),
        )
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="editorial-evidence") as pool:
            visual_future = pool.submit(
                analyze_media,
                media_path=Path(source["visual_path"]),
                media_kind="video",
                duration_sec=float(probe["duration_ms"]) / 1000.0,
                detail=detail,
                ffmpeg="ffmpeg",
                provider=provider,
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
        print(
            _message(
                project,
                f"Visual learning: coarse map complete ({len(output.get('segments', []))} visual range(s), "
                f"{len(acoustic_events)} acoustic cue(s)); reviewing key transitions...",
                f"映像学習: 概略マップが完了（映像区間 {len(output.get('segments', []))} 件、"
                f"音響キュー {len(acoustic_events)} 件）。重要な切り替わりを確認中…",
            ),
            flush=True,
        )
        try:
            bursts = analyze_temporal_bursts(
                Path(source["visual_path"]),
                duration_ms=int(probe["duration_ms"]),
                segments=output.get("segments", []),
                acoustic_events=acoustic_events,
                frame_differences=output.get("frame_differences", []),
                model=model,
                output_locale=str(project.get("output_locale", "en")),
            )
        except Exception as exc:
            print(
                _message(
                    project,
                    f"Warning: temporal frame-burst review failed; keeping coarse vision: {exc}",
                    f"警告: 時間的フレームバースト確認に失敗しました。概略の映像分析を維持します: {exc}",
                ),
                flush=True,
            )
            bursts = {"bursts": [], "cost_usd": 0.0, "prompt_version": "failed"}
        print(
            _message(
                project,
                f"Visual learning: transition review complete ({len(bursts.get('bursts', []))} burst(s)); "
                "updating reusable game knowledge...",
                f"映像学習: 切り替わりの確認が完了（バースト {len(bursts.get('bursts', []))} 件）。"
                "再利用可能なゲーム知識を更新中…",
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
        refiner = self._build_editorial_refiner(
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
        visuals = [
            VisualEvidence(
                start_ms=int(item.get("start_ms", 0)),
                end_ms=int(item.get("end_ms", 0)),
                description=str(item.get("description") or ""),
                tags=tuple(str(tag) for tag in item.get("tags", []) if str(tag).strip()),
                confidence=float(item.get("confidence", 0.0)),
                motion_level=(float(item["motion_level"]) if item.get("motion_level") is not None else None),
                visual_category=str(item.get("visual_category") or "other"),
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

        def record_completed_window(window: dict[str, Any]) -> None:
            completed = semantic_progress["completed_windows"]
            base_index = int(window["base_window_index"])
            completed[:] = [
                item for item in completed if int(item.get("base_window_index", -1)) != base_index
            ]
            completed.append(window)
            completed.sort(key=lambda item: int(item.get("base_window_index", -1)))
            _write_json_atomic(semantic_progress_path, semantic_progress)

        review: dict[str, Any] = {"cost_usd": 0.0}
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
                must_keep_notes=project["must_keep_notes"],
                de_emphasize_notes=project["de_emphasize_notes"],
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
            review_count = sum(
                1
                for item in result["recommendations"]
                if item.get("disposition") in {"omit", "condense", "review"}
                and int(item.get("end_ms", 0)) - int(item.get("start_ms", 0)) >= 8000
            )
            print(
                _message(
                    project,
                    "Editorial analysis: first pass complete; targeted visual review will inspect "
                    f"up to {min(12, review_count)} consequential suggestion(s)...",
                    "編集分析: 初回分析が完了。影響の大きい提案を最大 "
                    f"{min(12, review_count)} 件、映像と照合します…",
                ),
                flush=True,
            )
            try:
                review = review_editorial_candidates(
                    Path(source["visual_path"]),
                    duration_ms=int(probe["duration_ms"]),
                    recommendations=result["recommendations"],
                    transcript=[asdict(item) for item in transcript],
                    visual_segments=[asdict(item) for item in visuals],
                    temporal_bursts=visual.get("temporal_bursts", []),
                    acoustic_events=visual.get("acoustic_events", []),
                    game_knowledge=game_profile_context(
                        visual.get("game_knowledge", {})
                        if isinstance(visual.get("game_knowledge"), dict)
                        else {}
                    ),
                    model=self._editorial_model(),
                    output_locale=str(project.get("output_locale", "en")),
                )
            except Exception as exc:
                print(
                    _message(
                        project,
                        f"Warning: targeted cross-modal review failed; keeping first-pass suggestions: {exc}",
                        f"警告: 映像・音声の重点照合に失敗しました。初回提案を維持します: {exc}",
                    ),
                    flush=True,
                )
                review = {
                    "prompt_version": "failed",
                    "reviews": [],
                    "creative_suggestions": [],
                    "cost_usd": 0.0,
                }
            apply_targeted_reviews(result["recommendations"], review.get("reviews", []))
            result["targeted_reviews"] = review.get("reviews", [])
            result["creative_suggestions"].extend(review.get("creative_suggestions", []))
            result["creative_suggestions"] = deduplicate_creative_suggestions(
                result["creative_suggestions"]
            )
            aligned_tokens_path = transcription.get("aligned_tokens_path")
            emphasized = _align_emphasized_phrases(
                result.get("emphasized_phrases", []),
                Path(aligned_tokens_path) if isinstance(aligned_tokens_path, str) else None,
            )
            if project.get("subtitle_mode", "full") == "emphasis" and emphasized:
                print(
                    _message(
                        project,
                        f"Editorial analysis: selectively cleaning {len(emphasized)} emphasized phrase(s)...",
                        f"編集分析: 強調フレーズ {len(emphasized)} 件を選択的に整文中…",
                    ),
                    flush=True,
                )
                cleanup_refiner = build_refiner(
                    self._subtitle_cleanup_model_config(),
                    [],
                    usage,
                    self.options.workspace / source["source_id"] / "emphasis-cleanup",
                )
                if cleanup_refiner is None:
                    raise SubtitlerError("Selective emphasized-phrase cleanup requires a cleanup model")
                try:
                    cleaned = cleanup_refiner.refine(
                        [str(item["source_text"]) for item in emphasized]
                    )
                finally:
                    cleanup_refiner.close()
                for item, text in zip(emphasized, cleaned):
                    normalized = " ".join(str(text or "").split())[:240]
                    if normalized:
                        item["text"] = normalized
            result["emphasized_phrases"] = emphasized
            result["targeted_review_prompt_version"] = review.get("prompt_version")
            print(
                _message(
                    project,
                    f"Editorial analysis: targeted review complete ({len(review.get('reviews', []))} reviewed, "
                    f"{len(review.get('creative_suggestions', []))} added creative accent(s)).",
                    f"編集分析: 重点照合が完了（{len(review.get('reviews', []))} 件を確認、"
                    f"演出案 {len(review.get('creative_suggestions', []))} 件を追加）。",
                ),
                flush=True,
            )
        except Exception as exc:
            failure_output = {
                "api_cost_usd": usage.total_cost_usd + float(review.get("cost_usd", 0.0)),
                "api_usage": [row.__dict__ for row in usage.rows],
                "structured_response_diagnostics_path": str(diagnostics_path),
                "semantic_progress_path": str(semantic_progress_path),
            }
            setattr(exc, "editorial_failure_output", failure_output)
            raise
        finally:
            refiner.close()
        result["api_cost_usd"] = usage.total_cost_usd + float(review.get("cost_usd", 0.0))
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
            "emphasized_phrases": semantics.get("emphasized_phrases", []),
            "timeline_coverage": coverage,
            "connections": semantics.get("connections", []),
            "conflicts": [],
            "semantic_spans": semantics.get("semantic_spans", []),
        }


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
        needle = "".join(
            character
            for character in str(item.get("source_text") or "").casefold()
            if not character.isspace()
        )
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
        first = rows[char_tokens[match]]
        last = rows[char_tokens[match + len(needle) - 1]]
        try:
            start_ms = round(float(first["start"]) * 1000)
            end_ms = round(float(last["end"]) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        if end_ms <= start_ms:
            continue
        normalized = dict(item)
        normalized.update(
            {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timing_verified": True,
                "text": str(item.get("source_text") or ""),
            }
        )
        result.append(normalized)
    return result


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
        "semantic_stage_version": EDITORIAL_STAGE_VERSIONS["semantic_spans"],
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
