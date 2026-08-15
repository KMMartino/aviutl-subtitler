"""Checkpointed serial execution for long-form editorial projects."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol, Sequence

from .editorial_locale import editorial_locale, locale_label
from .editorial_project import (
    ASSET_CHECKPOINT_STAGE,
    CHECKPOINT_STAGES,
    load_editorial_checkpoint,
    next_incomplete_source,
    unresolved_editorial_sources,
    update_source_stage,
    write_editorial_checkpoint,
)
from .editorial_exo import write_editorial_exo
from .editorial_report import write_editorial_html
from .editorial_resume import prepare_editorial_resume, relink_matching_editorial_sources
from .errors import SubtitlerError


class EditorialStageExecutor(Protocol):
    def run_stage(
        self,
        stage: str,
        source: dict[str, Any],
        project: dict[str, Any],
        prior_outputs: dict[str, Any],
    ) -> Any: ...

    def finalize_project(self, project: dict[str, Any]) -> dict[str, Any]: ...

    def resolve_assets(self, project: dict[str, Any]) -> dict[str, Any]: ...


class EditorialRunInterrupted(SubtitlerError):
    """A durable project stopped after recording its failed stage."""


STAGE_LABELS = {
    "source_probe": "Source verification",
    "transcription": "Transcription and alignment",
    "visual_learning": "Visual and game learning",
    "semantic_spans": "Editorial mapping",
    "local_reconciliation": "Per-recording synthesis",
    "global_reconciliation": "Project-wide synthesis",
    "editorial_assets": "Editorial evidence lookup",
}

STAGE_LABELS_JA = {
    "source_probe": "素材確認",
    "transcription": "文字起こし・アラインメント",
    "visual_learning": "映像・ゲーム学習",
    "semantic_spans": "編集マッピング",
    "local_reconciliation": "録画別の統合",
    "global_reconciliation": "プロジェクト全体の統合",
    "editorial_assets": "編集用の根拠画像検索",
}


def run_editorial_project(
    checkpoint_path: Path,
    executor: EditorialStageExecutor,
    *,
    report_path: Path | None = None,
    exo_path: Path | None = None,
    restart_from: str | None = None,
    source_specs: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resume a project, processing one complete source before the next.

    Successful stage outputs are reused verbatim. A failure is recorded before
    control returns to the caller, so retry starts at the failed stage rather
    than paying for completed transcription or vision work again.
    """
    project = load_editorial_checkpoint(checkpoint_path)
    locale = editorial_locale(project.get("output_locale"))
    print(
        locale_label(
            locale,
            "Editorial API cost carried forward: ",
            "引き継いだ編集 API 費用: ",
        )
        + f"${float(project.get('run_provenance', {}).get('actual_cost_usd', 0.0)):.4f}",
        flush=True,
    )
    if source_specs is not None:
        relink_matching_editorial_sources(project, source_specs)
    unresolved = unresolved_editorial_sources(project)
    if unresolved:
        names = ", ".join(item["path"] for item in unresolved[:3])
        extra = "…" if len(unresolved) > 3 else ""
        raise SubtitlerError(
            locale_label(
                locale,
                "Editorial sources are missing or do not match their checkpoint fingerprints: "
                f"{names}{extra}. Relink the original source files before resuming.",
                "編集素材が見つからないか、チェックポイントのフィンガープリントと一致しません: "
                f"{names}{extra}。再開前に元の素材ファイルを再リンクしてください。",
            )
        )
    resolved_report = report_path or checkpoint_path.with_suffix(".html")
    resolved_exo = exo_path or checkpoint_path.with_suffix(".exo")
    invalidated_from = prepare_editorial_resume(project, restart_from)
    if invalidated_from is not None:
        print(
            locale_label(
                locale,
                f"Editorial checkpoint compatibility restart: {invalidated_from} and downstream stages",
                f"編集チェックポイントの互換性により再実行: {invalidated_from} 以降の段階",
            ),
            flush=True,
        )
        write_editorial_checkpoint(checkpoint_path, project)
        write_editorial_html(resolved_report, project)
    while (source := next_incomplete_source(project)) is not None:
        prior_outputs = {
            stage: checkpoint["output"]
            for stage, checkpoint in source["stages"].items()
            if checkpoint["status"] == "complete"
        }
        for stage in CHECKPOINT_STAGES:
            checkpoint = source["stages"][stage]
            if checkpoint["status"] == "complete":
                continue
            source_number = int(source.get("order", 0)) + 1
            stage_label = _stage_label(stage, locale)
            print(
                locale_label(
                    locale,
                    f"Editorial stage {CHECKPOINT_STAGES.index(stage) + 1}/{len(CHECKPOINT_STAGES) + 2} - "
                    f"{stage_label} started for {source['original_name']} "
                    f"(recording {source_number}/{len(project['sources'])}).",
                    f"編集段階 {CHECKPOINT_STAGES.index(stage) + 1}/{len(CHECKPOINT_STAGES) + 2} - "
                    f"{source['original_name']} の{stage_label}を開始 "
                    f"(録画 {source_number}/{len(project['sources'])})。",
                ),
                flush=True,
            )
            stage_started = time.monotonic()
            _ensure_cost_ceiling(project)
            update_source_stage(project, source["source_id"], stage, "in_progress")
            write_editorial_checkpoint(checkpoint_path, project)
            write_editorial_html(resolved_report, project)
            try:
                output = executor.run_stage(stage, source, project, dict(prior_outputs))
            except Exception as exc:
                failure_output = getattr(exc, "editorial_failure_output", None)
                update_source_stage(
                    project,
                    source["source_id"],
                    stage,
                    "failed",
                    output=failure_output,
                    error=str(exc),
                )
                if isinstance(failure_output, dict):
                    _record_stage_cost(
                        project,
                        source["source_id"],
                        stage,
                        failure_output,
                    )
                write_editorial_checkpoint(checkpoint_path, project)
                write_editorial_html(resolved_report, project)
                print(
                    locale_label(
                        locale,
                        f"Editorial stage failed after {time.monotonic() - stage_started:.1f}s: "
                        f"{source['original_name']} / {stage_label}.",
                        f"編集段階が {time.monotonic() - stage_started:.1f} 秒後に失敗: "
                        f"{source['original_name']} / {stage_label}。",
                    ),
                    flush=True,
                )
                raise EditorialRunInterrupted(
                    locale_label(
                        locale,
                        f"Editorial analysis stopped at {source['source_id']}/{stage}. "
                        f"Checkpoint: {checkpoint_path}. Resume with the same project after resolving: {exc}",
                        f"編集分析は {source['source_id']}/{stage} で停止しました。"
                        f"チェックポイント: {checkpoint_path}。問題を解決後、同じプロジェクトで再開してください: {exc}",
                    )
                ) from exc
            update_source_stage(
                project,
                source["source_id"],
                stage,
                "complete",
                output=output,
            )
            prior_outputs[stage] = output
            _apply_stage_output(project, source, stage, output)
            _record_stage_cost(project, source["source_id"], stage, output)
            print(
                locale_label(
                    locale,
                    f"Editorial stage complete in {time.monotonic() - stage_started:.1f}s: "
                    f"{source['original_name']} / {stage_label}.",
                    f"編集段階が {time.monotonic() - stage_started:.1f} 秒で完了: "
                    f"{source['original_name']} / {stage_label}。",
                ),
                flush=True,
            )
            write_editorial_checkpoint(checkpoint_path, project)
            write_editorial_html(resolved_report, project)
    global_checkpoint = project["editorial_map"]["global_reconciliation"]
    if global_checkpoint["status"] != "complete":
        global_label = _stage_label("global_reconciliation", locale)
        print(
            locale_label(
                locale,
                f"Editorial stage 6/7 - {global_label} started.",
                f"編集段階 6/7 - {global_label}を開始。",
            ),
            flush=True,
        )
        global_started = time.monotonic()
        _ensure_cost_ceiling(project)
        _update_global_checkpoint(global_checkpoint, "in_progress")
        project["editorial_map"]["status"] = "in_progress"
        write_editorial_checkpoint(checkpoint_path, project)
        write_editorial_html(resolved_report, project)
        try:
            final_map = executor.finalize_project(project)
        except Exception as exc:
            failure_output = getattr(exc, "editorial_failure_output", None)
            _update_global_checkpoint(
                global_checkpoint,
                "failed",
                output=failure_output,
                error=str(exc),
            )
            if isinstance(failure_output, dict):
                _record_stage_cost(
                    project,
                    "project",
                    "global_reconciliation",
                    failure_output,
                )
            project["editorial_map"]["status"] = "failed"
            write_editorial_checkpoint(checkpoint_path, project)
            write_editorial_html(resolved_report, project)
            print(
                locale_label(
                    locale,
                    "Editorial project-wide synthesis failed after "
                    f"{time.monotonic() - global_started:.1f}s.",
                    "プロジェクト全体の編集統合が "
                    f"{time.monotonic() - global_started:.1f} 秒後に失敗しました。",
                ),
                flush=True,
            )
            raise EditorialRunInterrupted(
                locale_label(
                    locale,
                    "Editorial analysis completed every source but stopped during global reconciliation. "
                    f"Checkpoint: {checkpoint_path}. Resume after resolving: {exc}",
                    "すべての素材分析は完了しましたが、プロジェクト全体の統合中に停止しました。"
                    f"チェックポイント: {checkpoint_path}。問題を解決後に再開してください: {exc}",
                )
            ) from exc
        if not isinstance(final_map, dict):
            raise EditorialRunInterrupted(
                locale_label(
                    locale,
                    "Global editorial reconciliation returned no usable artifact",
                    "プロジェクト全体の編集統合から利用可能な成果物が返されませんでした",
                )
            )
        for field in (
            "global_threads",
            "connections",
            "conflicts",
            "duration_budget",
            "editorial_direction_summary",
            "optimal_plan",
            "director_review",
            "director_model",
            "final_actions",
            "supporting_edits",
            "editorial_threads",
        ):
            if field in final_map:
                project["editorial_map"][field] = final_map[field]
        _record_stage_cost(project, "project", "global_reconciliation", final_map)
        _update_global_checkpoint(global_checkpoint, "complete", output=final_map)
        print(
            locale_label(
                locale,
                f"Editorial stage complete in {time.monotonic() - global_started:.1f}s: "
                f"{global_label}.",
                f"編集段階が {time.monotonic() - global_started:.1f} 秒で完了: {global_label}。",
            ),
            flush=True,
        )
    asset_checkpoint = project["editorial_map"][ASSET_CHECKPOINT_STAGE]
    if asset_checkpoint["status"] != "complete":
        asset_label = _stage_label(ASSET_CHECKPOINT_STAGE, locale)
        print(
            locale_label(
                locale,
                f"Editorial stage 7/7 - {asset_label} started.",
                f"編集段階 7/7 - {asset_label}を開始。",
            ),
            flush=True,
        )
        asset_started = time.monotonic()
        _ensure_cost_ceiling(project)
        _update_global_checkpoint(asset_checkpoint, "in_progress")
        write_editorial_checkpoint(checkpoint_path, project)
        write_editorial_html(resolved_report, project)
        try:
            asset_output = executor.resolve_assets(project)
        except Exception as exc:
            failure_output = getattr(exc, "editorial_failure_output", None)
            _update_global_checkpoint(
                asset_checkpoint, "failed", output=failure_output, error=str(exc)
            )
            if isinstance(failure_output, dict):
                _record_stage_cost(project, "project", ASSET_CHECKPOINT_STAGE, failure_output)
            project["editorial_map"]["status"] = "failed"
            write_editorial_checkpoint(checkpoint_path, project)
            write_editorial_html(resolved_report, project)
            raise EditorialRunInterrupted(
                locale_label(
                    locale,
                    "The editorial plan is complete, but evidence lookup stopped. "
                    f"Checkpoint: {checkpoint_path}. Resume to retry only evidence lookup: {exc}",
                    "編集プランは完了しましたが、根拠画像の検索中に停止しました。"
                    f"チェックポイント: {checkpoint_path}。根拠画像検索だけを再試行できます: {exc}",
                )
            ) from exc
        if not isinstance(asset_output, dict):
            raise EditorialRunInterrupted("Editorial evidence lookup returned no usable artifact")
        if isinstance(asset_output.get("supporting_edits"), list):
            project["editorial_map"]["supporting_edits"] = asset_output["supporting_edits"]
        if isinstance(asset_output.get("editorial_assets"), list):
            project["editorial_map"]["assets"] = asset_output["editorial_assets"]
        _record_stage_cost(project, "project", ASSET_CHECKPOINT_STAGE, asset_output)
        _update_global_checkpoint(asset_checkpoint, "complete", output=asset_output)
        print(
            locale_label(
                locale,
                f"Editorial stage complete in {time.monotonic() - asset_started:.1f}s: {asset_label}.",
                f"編集段階が {time.monotonic() - asset_started:.1f} 秒で完了: {asset_label}。",
            ),
            flush=True,
        )
    project["editorial_map"]["status"] = "complete"
    write_editorial_checkpoint(checkpoint_path, project)
    write_editorial_html(resolved_report, project)
    try:
        write_editorial_exo(resolved_exo, project)
    except Exception as exc:
        raise EditorialRunInterrupted(
            locale_label(
                locale,
                "Editorial analysis is complete, but its AviUtl EXO could not be written. "
                f"Checkpoint: {checkpoint_path}. Resume to retry only this output: {exc}",
                "編集分析は完了しましたが、AviUtl EXO を書き出せませんでした。"
                f"チェックポイント: {checkpoint_path}。この出力だけを再試行するには再開してください: {exc}",
            )
        ) from exc
    project["outputs"] = {
        "html_path": str(resolved_report),
        "exo_path": str(resolved_exo),
    }
    write_editorial_checkpoint(checkpoint_path, project)
    write_editorial_html(resolved_report, project)
    print(
        locale_label(locale, "Editorial run complete. Total API cost: ", "編集処理が完了しました。API 費用合計: ")
        + f"${float(project.get('run_provenance', {}).get('actual_cost_usd', 0.0)):.4f}",
        flush=True,
    )
    return project


def _update_global_checkpoint(
    checkpoint: dict[str, Any], status: str, *, output: Any = None, error: str = ""
) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    if status == "in_progress":
        checkpoint["attempts"] += 1
        checkpoint["started_at_utc"] = now
        checkpoint["completed_at_utc"] = None
    else:
        checkpoint["completed_at_utc"] = now
    checkpoint["status"] = status
    checkpoint["error"] = error.strip()[:4000]
    if output is not None:
        checkpoint["output"] = output


def _apply_stage_output(
    project: dict[str, Any], source: dict[str, Any], stage: str, output: Any
) -> None:
    if stage == "semantic_spans" and isinstance(output, dict):
        context = output.get("cumulative_context")
        if isinstance(context, dict):
            project["cumulative_context"] = context
    if stage != "local_reconciliation" or not isinstance(output, dict):
        return
    source["result"] = output
    editorial_map = project["editorial_map"]
    for field in (
        "global_threads",
        "recommendations",
        "narration_briefs",
        "creative_suggestions",
        "emphasized_phrases",
        "timeline_coverage",
        "connections",
        "conflicts",
    ):
        values = output.get(field)
        if isinstance(values, list):
            editorial_map[field].extend(values)


def _record_stage_cost(
    project: dict[str, Any], source_id: str, stage: str, output: Any
) -> None:
    if not isinstance(output, dict):
        return
    raw_cost = output.get("api_cost_usd")
    if raw_cost is None and stage == "visual_learning":
        raw_cost = output.get("cost_usd")
    try:
        cost = max(0.0, float(raw_cost or 0.0))
    except (TypeError, ValueError):
        cost = 0.0
    if not cost:
        return
    provenance = project["run_provenance"]
    provenance["actual_cost_usd"] = float(provenance.get("actual_cost_usd", 0.0)) + cost
    provenance["runs"].append(
        {"source_id": source_id, "stage": stage, "actual_cost_usd": cost}
    )
    print(
        locale_label(
            project.get("output_locale"),
            f"Editorial API cost: ${provenance['actual_cost_usd']:.4f} total "
            f"(+${cost:.4f} {source_id}/{stage})",
            f"編集 API 費用: 合計 ${provenance['actual_cost_usd']:.4f} "
            f"(+${cost:.4f} {source_id}/{stage})",
        ),
        flush=True,
    )


def _ensure_cost_ceiling(project: dict[str, Any]) -> None:
    provenance = project["run_provenance"]
    source_hours = sum(int(source["duration_ms"]) for source in project["sources"]) / 3_600_000.0
    ceiling = source_hours * float(provenance.get("max_cost_per_source_hour_usd", 10.0))
    actual = float(provenance.get("actual_cost_usd", 0.0))
    if actual >= ceiling:
        raise SubtitlerError(
            locale_label(
                project.get("output_locale"),
                f"Editorial API cost ceiling reached (${actual:.2f} of ${ceiling:.2f}). "
                "Resume only after explicitly revising the project cost policy.",
                f"編集 API 費用の上限に達しました (${actual:.2f} / ${ceiling:.2f})。"
                "プロジェクトの費用設定を明示的に変更してから再開してください。",
            )
        )


def _stage_label(stage: str, locale: str) -> str:
    return STAGE_LABELS_JA[stage] if editorial_locale(locale) == "ja" else STAGE_LABELS[stage]
