"""Concrete hosted stages for the checkpointed editorial project runner."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator

from .api_usage import ApiUsageLedger
from .artifact_io import write_json_artifact
from .config import load_workflow_config, validate_workflow_config
from .evidence import TranscriptEvidence, VisualEvidence, load_transcript_evidence
from .editorial_analysis import (
    EDITORIAL_PROMPT_VERSION,
    analyze_editorial_source,
    select_editorial_subtitles,
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
from .editorial_guidance import project_brief
from .operation_store import content_digest
from .env import load_env_file
from .errors import SubtitlerError
from .game_knowledge import (
    game_profile_context,
    load_game_profile,
    update_game_profile,
)
from .glossary import load_glossary
from .source_inspection import SourceInspectionRequest, inspect_recording
from .game_wiki import game_title_matches, lookup_game_wiki
from .editorial_visual import OpenAIEditorialVisualProvider
from .media_analysis import (
    AnalysisSegment,
    MediaAnalysisResponseError,
    MediaAnalysisResult,
    analyze_media,
)
from .subtitle_stage import build_refiner
from .speech_editing import align_selected_phrases, clean_selected_subtitles, tighten_transcript_to_speech
from .transcript_document import load_transcript_document
from .transcript_workflow import run_transcript_workflow


EDITORIAL_PROGRESS_FIRST_UPDATE_SECONDS = 20.0
EDITORIAL_PROGRESS_UPDATE_INTERVAL_SECONDS = 30.0
EDITORIAL_VISUAL_WINDOW_SECONDS = 12 * 60.0
MAX_EDITORIAL_VISUAL_WORKERS = 3
MAX_EDITORIAL_VISUAL_SPLIT_DEPTH = 2


@dataclass(frozen=True)
class HostedEditorialExecutorOptions:
    config_path: Path
    env_file: Path
    workspace: Path
    audio_track: int = 0
    glossary_path: Path | None = None
    game_knowledge_path: Path | None = None
    transcript_artifacts: tuple[Path, ...] = ()


class HostedEditorialStageExecutor:
    """Run generic transcript, visual, and semantic stages for one source."""

    def __init__(self, options: HostedEditorialExecutorOptions) -> None:
        self.options = options
        self.adaptive_artifact_workspace = options.workspace / "adaptive-artifacts"
        self.recommendation_artifact_workspace = options.workspace / 'recommendation-artifacts'
        self.local_evidence_workspace = options.workspace / 'local-evidence'
        self.options.workspace.mkdir(parents=True, exist_ok=True)
        load_env_file(options.env_file)
        self.config = load_workflow_config("hosted-long-stream", options.config_path)
        self.transcript_artifacts: dict[tuple[Path, int], Path] = {}
        for artifact in options.transcript_artifacts:
            document = load_transcript_document(artifact)
            key = (Path(document.source_path).resolve(), document.audio_track)
            if key in self.transcript_artifacts:
                raise SubtitlerError(f"Multiple transcripts supplied for {document.source_path}")
            document.require_reusable(key[0], key[1])
            self.transcript_artifacts[key] = artifact
        validate_workflow_config(
            self.config,
            workflow="hosted-long-stream",
            check_paths=False,
        )

    def prepare_project(self, project: dict[str, Any]) -> None:
        for source in project["sources"]:
            paired = source.get("media_mode") == "paired"
            source["audio_track"] = 0 if paired else self.options.audio_track
            source["game_audio_track"] = 0 if paired else self.config.get("editorial", {}).get("game_audio_track")
            speech_path = Path(source["visual_path"] if source.get("speech_source") == "gameplay" else source["audio_path"])
            tracks = [(speech_path, source["audio_track"])]
            game_track = source["game_audio_track"]
            if game_track is not None:
                tracks.append((Path(source["visual_path"]), game_track))
            for path, track in tracks:
                probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                        "stream=index", "-of", "json", str(path)], capture_output=True, text=True, check=False)
                if probe.returncode or type(track) is not int or track < 0 or track >= len(json.loads(probe.stdout).get("streams", [])):
                    raise SubtitlerError(f"Audio track {track + 1} is unavailable in {path.name}")

    def operation_parameters(self, stage: str) -> dict[str, Any]:
        """Only parameters consumed by this operation belong in its reuse contract."""
        if stage == "source_probe":
            return {"audio_track": self.options.audio_track, "game_audio_track": self.config.get("editorial", {}).get("game_audio_track")}
        if stage == "transcription":
            return {
                **{key: self.config.get(key, {}) for key in ("backend", "audio", "vad", "alignment", "workflow", "cost")},
                "audio_track": self.options.audio_track,
                "glossary": [asdict(entry) for entry in load_glossary(self.options.glossary_path)],
            }
        if stage == "local_reconciliation":
            return {}
        editorial = self.config.get("editorial", {})
        if stage == "visual_learning":
            return {"model": self._model_config("analysis")["cleanup"],
                    "visual_reasoning_effort": self._visual_reasoning_effort(),
                    "detail": editorial.get("visual_detail"), "sampling_scale": editorial.get("visual_sampling_scale"),
                    "audio_track": self.options.audio_track}
        if stage == "global_reconciliation":
            return {"mode": "collected_activity_overview"}
        if stage == "action_planning":
            return {"adaptive": {key: editorial.get(key) for key in ("cutting_mode", "game_audio_track", "collection_model", "cutting_model", "escalation_model", "trim_utterance_pauses", "gap_edge_mode", "voice_gap_min_ms", "voice_leading_handle_ms", "voice_trailing_handle_ms", "recommendations_enabled", "recommendation_model")},
                    "selection": self._model_config("analysis")["cleanup"],
                    "cleanup": self._model_config("subtitle_cleanup")["cleanup"],
                    "glossary": [asdict(entry) for entry in load_glossary(self.options.glossary_path)]}
        return {"model": self._model_config("analysis")["cleanup"]}

    @contextmanager
    def operation_scope(self, generation: str) -> Iterator[None]:
        # A new input revision must never inherit mutable partial-window caches.
        original = self.options
        self.options = replace(original, workspace=original.workspace / "operations" / generation)
        self.options.workspace.mkdir(parents=True, exist_ok=True)
        try:
            yield
        finally:
            self.options = original

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

    def generate_narration(self, project: dict[str, Any]) -> dict[str, Any]:
        from .editorial_narration import generate_narration
        return generate_narration(project, self.options.workspace / 'narration-artifacts', self.config.get('editorial', {}))

    def finalize_project(self, project: dict[str, Any]) -> dict[str, Any]:
        """Expose collected structure without a paid automatic-story/narration plan."""
        from .editorial_recommendations import factual_overview
        return factual_overview(project)

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
        documents = {}
        speech_activity = {}
        for source in project["sources"]:
            transcription = source.get("stages", {}).get("transcription", {}).get("output")
            transcript_path = transcription.get("transcript_path") if isinstance(transcription, dict) else None
            if not transcript_path:
                raise SubtitlerError("Voice-gap planning requires a durable transcript with speech detection")
            document = load_transcript_document(Path(transcript_path))
            documents[source["source_id"]] = document
            speech_activity[source["source_id"]] = (
                [(round(item.start * 1000), round(item.end * 1000)) for item in document.backend.raw_vad_speech_intervals]
                or document.speech_activity_ms()
            )
        settings = {**self.config.get("editorial", {}), "gap_edge_mode": "acoustic"}
        voice_energy = {}
        if settings.get("gap_edge_mode", "fixed") == "acoustic":
            from .acoustic_edges import load_voice_energy
            for source_id, document in documents.items():
                try:
                    voice_energy[source_id] = load_voice_energy(Path(document.source_path), document.audio_track,
                        getattr(self, 'local_evidence_workspace', self.options.workspace) / "voice-energy")
                except (OSError, SubtitlerError) as exc:
                    print(f"Voice energy unavailable for {source_id}; using protective fixed padding: {exc}", flush=True)
        actionable = build_human_information_plan(project=project, synthesis={**synthesis, "narration_briefs": []},
            speech_activity=speech_activity, settings=settings, voice_energy=voice_energy)
        from .editorial_recommendations import evidence_catalog, generate_recommendations
        try:
            recommendations = (generate_recommendations(project, getattr(self, 'recommendation_artifact_workspace', self.options.workspace), settings, usage)
                if settings.get("recommendations_enabled", True)
                else {"schema_version": 1, "type": "editor_recommendations", "executable": False,
                      "catalog": evidence_catalog(project), "assessments": []})
        except Exception as exc:
            setattr(exc, "editorial_failure_output", {"api_cost_usd": usage.total_cost_usd,
                "api_usage": [asdict(row) for row in usage.rows]})
            raise
        actionable.update(cutting_mode="voice_gaps", editor_recommendations=recommendations,
                          gap_edge_mode=settings.get("gap_edge_mode", "fixed"), narration_briefs=[])
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
            document = documents[source_id]
            emphasized_phrases.extend(
                align_selected_phrases(
                    [
                        item
                        for item in selected
                        if str(item.get("source_id")) == source_id
                    ],
                    document.aligned_tokens(),
                    document.speech_activity_ms(),
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
                emphasized_phrases = clean_selected_subtitles(
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
            "estimated_final_ms": actionable["estimated_final_ms"],
            "narration_replaced_ms": 0,
            "prompt_version": actionable["prompt_version"],
            "api_cost_usd": usage.total_cost_usd,
            "api_usage": [row.__dict__ for row in usage.rows],
        }
        result.update({key: actionable[key] for key in ("cutting_mode", "adaptive_report_path",
                       "adaptive_artifact_path", "baseline_confirmed_cuts", "narration_briefs",
                       "editor_recommendations", "gap_edge_mode") if key in actionable})
        print(
            _message(
                project,
                f"Human editing guides: complete with {len(result['confirmed_cuts'])} cut marker(s), "
                f"{len(result['final_actions'])} narration brief(s), and "
                f"{len(emphasized_phrases)} display subtitle(s).",
                f"人間向け編集ガイド: カットマーカー {len(result['confirmed_cuts'])} 件、"
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

    def _model_config(self, role: str) -> dict[str, Any]:
        """Configure the same hosted process for analysis, synthesis, or cleanup."""
        model_default, reasoning_key, reasoning_default = {
            "analysis": ("gpt-5.6-luna", "reasoning_effort", "medium"),
            "director": ("gpt-5.6-terra", "director_reasoning_effort", "low"),
            "subtitle_cleanup": ("gpt-5.6-luna", "subtitle_cleanup_reasoning_effort", "low"),
        }[role]
        config = json.loads(json.dumps(getattr(self, "config", {})))
        editorial = config.get("editorial") or {}
        config.setdefault("cleanup", {}).update(
            backend="openai", api_model=str(editorial.get(f"{role}_model") or model_default),
            reasoning_effort=str(editorial.get(reasoning_key) or reasoning_default), thinking_level=None,
        )
        return config

    def _editorial_model(self) -> str:
        cleanup = self._model_config("analysis")["cleanup"]
        return str(cleanup["api_model"])

    def _visual_reasoning_effort(self) -> str:
        editorial = self.config.get("editorial")
        if not isinstance(editorial, dict):
            editorial = {}
        value = str(editorial.get("visual_reasoning_effort") or "low")
        return value if value in {"none", "low", "medium", "high", "xhigh", "max"} else "low"



    def _director_model(self) -> str:
        return str(self._model_config("director")["cleanup"]["api_model"])


    def _build_editorial_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        return build_refiner(self._model_config("analysis"), [], usage, sidecar_base)

    def _build_director_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        return build_refiner(self._model_config("director"), [], usage, sidecar_base)


    def _build_game_learning_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        """Reserve the output budget for the compact profile rather than deliberation."""
        config = self._model_config("analysis")
        config["cleanup"]["reasoning_effort"] = "low"
        return build_refiner(config, [], usage, sidecar_base)

    def _build_subtitle_cleanup_refiner(
        self, usage: ApiUsageLedger, sidecar_base: Path
    ) -> Any:
        return build_refiner(
            self._model_config("subtitle_cleanup"),
            load_glossary(self.options.glossary_path),
            usage,
            sidecar_base,
        )

    def _probe(self, source: dict[str, Any]) -> dict[str, Any]:
        result = inspect_recording(SourceInspectionRequest(
            audio_path=Path(source["audio_path"]), visual_path=Path(source["visual_path"]),
            paired=source["media_mode"] == "paired", expected_visual_duration_ms=int(source["visual_duration_ms"]),
        ))
        visual, audio = result.visual_geometry, result.audio_geometry
        return {
            "duration_ms": result.visual_duration_ms,
            "audio_duration_ms": result.audio_duration_ms,
            "visual_duration_ms": result.visual_duration_ms,
            "frame_rate": result.frame_rate or source.get("frame_rate"),
            "media_mode": source["media_mode"],
            "audio_path": source["audio_path"], "visual_path": source["visual_path"],
            "visual_width": visual.width if visual else source.get("width"),
            "visual_height": visual.height if visual else source.get("height"),
            "audio_width": audio.width if audio else source.get("audio_width"),
            "audio_height": audio.height if audio else source.get("audio_height"),
            "wide_layout": result.wide_layout.to_dict() if result.wide_layout else None,
        }

    def _transcribe(
        self, source: dict[str, Any], project: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        source_workspace = self.options.workspace / source["source_id"]
        source_workspace.mkdir(parents=True, exist_ok=True)
        result = run_transcript_workflow(
            source_path=Path(source["visual_path"] if source.get("speech_source") == "gameplay" else source["audio_path"]),
            config=self._model_config("subtitle_cleanup"),
            workspace=source_workspace,
            audio_track=source.get("audio_track", self.options.audio_track),
            glossary=load_glossary(self.options.glossary_path),
            reuse_document=self.transcript_artifacts.get((Path(source["visual_path"] if source.get("speech_source") == "gameplay" else source["audio_path"]).resolve(), source.get("audio_track", self.options.audio_track))),
        )
        transcript = load_transcript_evidence(result.document_path)
        return {
            "document_path": str(result.document_path),
            "document_revision": result.document.revision_id,
            "transcript_path": str(result.transcript_path),
            "subtitle_mode": "full",
            "speech_segments": len(transcript),
            "first_speech_ms": transcript[0].start_ms if transcript else None,
            "last_speech_ms": transcript[-1].end_ms if transcript else None,
            "api_cost_usd": result.api_cost_usd,
            "api_usage_path": str(result.api_usage_path),
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
        if reference_context.get("status") != "complete" or not game_title_matches(
            str(project["title_or_game"]), str(reference_context.get("page_title") or "")
        ):
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
                output_locale=str(project.get("processing_locale", "en")),
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
                Path(source["visual_path"] if source.get("speech_source") == "gameplay" else source["audio_path"]),
                duration_ms=int(probe["duration_ms"]),
                audio_track=source.get("audio_track", self.options.audio_track),
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
            evidence = load_transcript_evidence(
                Path(transcription["document_path"]),
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
                    output_locale=str(project.get("processing_locale", "en")),
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
        transcript = load_transcript_evidence(
            Path(transcription["document_path"]),
        )
        transcript = tighten_transcript_to_speech(
            transcript,
            load_transcript_document(Path(transcription["transcript_path"])).speech_activity_ms(),
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
            evidence_identity=content_digest({"transcript": [asdict(item) for item in transcript],
                "visual": visual, "brief": project_brief(project), "cumulative_context": project["cumulative_context"],
                "parameters": self.operation_parameters("semantic_spans")}),
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
                write_json_artifact(semantic_progress_path, semantic_progress)

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
                output_locale=str(project.get("processing_locale", "en")),
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
        "processing_locale": output_locale,
        "editorial_context": editorial_context,
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
            write_json_artifact(
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
            _print_console_safe(
                locale_label(
                    output_locale,
                    f"Visual learning: retrying {_visual_clock(start)}-{_visual_clock(end)} "
                    "as two smaller structured requests...",
                    f"映像学習: {_visual_clock(start)}-{_visual_clock(end)} を、"
                    "2 件の小さな構造化リクエストに分けて再試行します…",
                ),
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


def _load_semantic_progress(
    path: Path,
    *,
    source_id: str,
    source_duration_ms: int,
    evidence_identity: str = "",
) -> dict[str, Any]:
    expected = {
        "evidence_identity": evidence_identity,
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


def _print_console_safe(message: str) -> None:
    """Log progress without letting a legacy console encoding stop the run."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)


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
