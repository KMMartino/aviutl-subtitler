"""Checkpointed serial execution for long-form editorial projects."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .editorial_locale import editorial_locale, locale_label
from .editorial_project import (
    ACTION_CHECKPOINT_STAGE,
    GLOBAL_OUTPUT_FIELDS,
    ACTION_OUTPUT_FIELDS,
    ASSET_CHECKPOINT_STAGE,
    CHECKPOINT_STAGES,
    PROJECT_CHECKPOINT_STAGES,
    load_editorial_checkpoint,
    next_incomplete_source,
    unresolved_editorial_sources,
    update_source_stage,
    write_editorial_checkpoint,
)
from .editorial_exo import write_editorial_exo_parts
from .editorial_report import write_editorial_html
from .editorial_resume import prepare_editorial_resume, relink_matching_editorial_sources
from .errors import SubtitlerError
from .editorial_operations import EditorialOperations


class EditorialStageExecutor(Protocol):
    def run_stage(
        self,
        stage: str,
        source: dict[str, Any],
        project: dict[str, Any],
        prior_outputs: dict[str, Any],
    ) -> Any: ...

    def finalize_project(self, project: dict[str, Any]) -> dict[str, Any]: ...

    def plan_actions(self, project: dict[str, Any]) -> dict[str, Any]: ...

    def resolve_assets(self, project: dict[str, Any]) -> dict[str, Any]: ...


class EditorialRunInterrupted(SubtitlerError):
    """A durable project stopped after recording its failed stage."""


STAGE_LABELS = {
    "source_probe": "Source verification",
    "transcription": "Transcription and alignment",
    "visual_learning": "Visual and game learning",
    "semantic_spans": "Event and story mapping",
    "local_reconciliation": "Per-recording synthesis",
    "global_reconciliation": "Factual story synthesis",
    "action_planning": "Human editing guides",
    "editorial_assets": "Dashboard preparation",
}

STAGE_LABELS_JA = {
    "source_probe": "素材確認",
    "transcription": "文字起こし・アラインメント",
    "visual_learning": "映像・ゲーム学習",
    "semantic_spans": "イベント・構成マッピング",
    "local_reconciliation": "録画別の統合",
    "global_reconciliation": "事実ベースのストーリー統合",
    "action_planning": "人間向け編集ガイド",
    "editorial_assets": "ダッシュボードの準備",
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
    getattr(executor, "prepare_project", lambda _project: None)(project)
    locale = editorial_locale(project.get("output_locale"))
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
    operations = EditorialOperations(checkpoint_path, project, executor)
    changed_inputs = operations.prepare()
    if changed_inputs is not None and (invalidated_from is None or
            (*CHECKPOINT_STAGES, *PROJECT_CHECKPOINT_STAGES).index(changed_inputs) <
            (*CHECKPOINT_STAGES, *PROJECT_CHECKPOINT_STAGES).index(invalidated_from)):
        invalidated_from = changed_inputs
    print(
        locale_label(
            locale,
            "Current end-to-end editorial API cost: ",
            "現在の編集処理全体の API 費用: ",
        )
        + f"${float(project.get('run_provenance', {}).get('actual_cost_usd', 0.0)):.4f}",
        flush=True,
    )
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
            output = _run_editorial_stage(
                project, stage, checkpoint,
                lambda: executor.run_stage(stage, source, project, dict(prior_outputs)),
                (), checkpoint_path, resolved_report, locale, operations, source=source,
            )
            prior_outputs[stage] = output
    project_stages = (
        ("global_reconciliation", executor.finalize_project, GLOBAL_OUTPUT_FIELDS),
        (ACTION_CHECKPOINT_STAGE, executor.plan_actions, ACTION_OUTPUT_FIELDS),
        (ASSET_CHECKPOINT_STAGE, executor.resolve_assets, ("supporting_edits", "editorial_assets")),
    )
    for stage, produce, output_fields in project_stages:
        checkpoint = project["editorial_map"][stage]
        if checkpoint["status"] == "complete":
            continue
        _run_editorial_stage(project, stage, checkpoint, lambda: produce(project), output_fields,
                           checkpoint_path, resolved_report, locale, operations)
    # Independent enrichment: existing paid action-planning artifacts remain valid.
    narrate = getattr(executor, 'generate_narration', None)
    if callable(narrate):
        from .editorial_narration import apply_narration
        try:
            narration = narrate(project)
            apply_narration(project, narration)
            _record_stage_cost(project, 'project', 'narration_suggestions', narration)
        except Exception as exc:
            _record_stage_cost(project, 'project', 'narration_suggestions', getattr(exc, 'editorial_failure_output', {}))
            write_editorial_checkpoint(checkpoint_path, project)
            raise EditorialRunInterrupted(f'Narration stopped; completed editing recommendations are preserved: {exc}') from exc
    project["editorial_map"]["status"] = "complete"
    write_editorial_checkpoint(checkpoint_path, project)
    write_editorial_html(resolved_report, project)
    try:
        from .editorial_audio import prepare_editorial_audio
        prepare_editorial_audio(project, resolved_exo.with_suffix(".audio"))
        write_editorial_checkpoint(checkpoint_path, project)
        exo_parts = write_editorial_exo_parts(resolved_exo, project)
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
        "exo_parts": exo_parts,
    }
    write_editorial_checkpoint(checkpoint_path, project)
    write_editorial_html(resolved_report, project)
    print(
        locale_label(
            locale,
            "Editorial run complete. End-to-end API cost: ",
            "編集処理が完了しました。処理全体の API 費用: ",
        )
        + f"${float(project.get('run_provenance', {}).get('actual_cost_usd', 0.0)):.4f}",
        flush=True,
    )
    return project




def _run_editorial_stage(
    project: dict[str, Any], stage: str, checkpoint: dict[str, Any],
    produce: Callable[[], Any], output_fields: tuple[str, ...],
    checkpoint_path: Path, report_path: Path, locale: str, operations: EditorialOperations,
    *, source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = _stage_label(stage, locale)
    number = (CHECKPOINT_STAGES.index(stage) + 1 if source is not None else
              len(CHECKPOINT_STAGES) + PROJECT_CHECKPOINT_STAGES.index(stage) + 1)
    source_id = source["source_id"] if source is not None else "project"
    if source is not None:
        label = f"{source['original_name']} / {label}"

    def transition(status, output=None, error=""):
        if source is not None:
            update_source_stage(project, source_id, stage, status, output=output, error=error)
        else:
            _update_global_checkpoint(checkpoint, status, output=output, error=error)
    print(locale_label(locale, f"Editorial stage {number}/8 - {label} started.",
                       f"編集段階 {number}/8 - {label}を開始。"), flush=True)
    started = time.monotonic()
    _ensure_cost_ceiling(project)
    transition("in_progress")
    project["editorial_map"]["status"] = "in_progress"
    write_editorial_checkpoint(checkpoint_path, project)
    write_editorial_html(report_path, project)
    try:
        output = operations.execute(stage, source, checkpoint, produce,
                                    lambda: write_editorial_checkpoint(checkpoint_path, project))
        if not isinstance(output, dict):
            raise SubtitlerError(f"{label} returned no usable artifact")
    except Exception as exc:
        failure_output = getattr(exc, "editorial_failure_output", None)
        transition("failed", output=failure_output, error=str(exc))
        _record_stage_cost(project, source_id, stage, failure_output)
        project["editorial_map"]["status"] = "failed"
        write_editorial_checkpoint(checkpoint_path, project)
        write_editorial_html(report_path, project)
        raise EditorialRunInterrupted(locale_label(
            locale,
            f"Editorial analysis stopped during {label}. Checkpoint: {checkpoint_path}. "
            f"Resume to retry this stage: {exc}",
            f"{label}中に停止しました。チェックポイント: {checkpoint_path}。"
            f"この段階から再開できます: {exc}",
        )) from exc
    if source is not None:
        _apply_stage_output(project, source, stage, output)
    for field in output_fields:
        if field in output:
            project["editorial_map"]["assets" if field == "editorial_assets" else field] = output[field]
    _record_stage_cost(project, source_id, stage, output)
    transition("complete", output=output)
    write_editorial_checkpoint(checkpoint_path, project)
    write_editorial_html(report_path, project)
    print(locale_label(locale, f"Editorial stage complete in {time.monotonic() - started:.1f}s: {label}.",
                       f"編集段階が {time.monotonic() - started:.1f} 秒で完了: {label}。"), flush=True)
    return output


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
    if raw_cost is None:
        return
    try:
        cost = max(0.0, float(raw_cost or 0.0))
    except (TypeError, ValueError):
        cost = 0.0
    provenance = project["run_provenance"]
    runs = [
        row
        for row in provenance.get("runs", [])
        if not (
            isinstance(row, dict)
            and str(row.get("source_id")) == source_id
            and str(row.get("stage")) == stage
        )
    ]
    if cost:
        runs.append({"source_id": source_id, "stage": stage, "actual_cost_usd": cost})
    provenance["runs"] = runs
    provenance["actual_cost_usd"] = sum(
        max(0.0, float(row.get("actual_cost_usd", 0.0)))
        for row in runs
        if isinstance(row, dict)
    )
    print(
        locale_label(
            project.get("output_locale"),
            f"End-to-end editorial API cost: ${provenance['actual_cost_usd']:.4f} "
            f"(${cost:.4f} {source_id}/{stage})",
            f"編集処理全体の API 費用: ${provenance['actual_cost_usd']:.4f} "
            f"(${cost:.4f} {source_id}/{stage})",
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
