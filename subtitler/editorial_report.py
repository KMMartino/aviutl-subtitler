"""Human-readable report generation for suggestion-only editorial artifacts."""

from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path
from typing import Any

from .editorial_presentation import (
    PresentedEditorialItem,
    category_label,
    presented_editorial_items,
    primary_suggestion,
)
from .editorial_locale import editorial_locale, locale_label
from .editorial_project import validate_editorial_project


def write_editorial_html(path: Path, artifact: dict[str, Any]) -> None:
    validate_editorial_project(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    presented = presented_editorial_items(artifact)
    screenshots = _write_editorial_screenshots(path, presented)
    path.write_text(render_editorial_html(artifact, screenshots), encoding="utf-8")


def render_editorial_html(
    artifact: dict[str, Any], screenshot_urls: dict[str, str] | None = None
) -> str:
    validate_editorial_project(artifact)
    locale = editorial_locale(artifact.get("output_locale"))
    sources = sorted(artifact["sources"], key=lambda item: item["order"])
    recommendations = artifact.get("editorial_map", {}).get("recommendations", [])
    presented = presented_editorial_items(artifact)
    screenshots = screenshot_urls or {}
    source_cards = "".join(_source_card(source, locale) for source in sources)
    grouped, unmatched = _group_linked_editorial_items(presented)
    recommendation_cards = "".join(
        _editorial_group_card(recommendation, linked, screenshots, locale)
        for recommendation, linked in grouped
    ) or f'<p class="empty">{_tr(locale, "No editorial recommendations have been generated yet.", "編集提案はまだ生成されていません。")}</p>'
    unmatched_cards = "".join(
        _standalone_linked_card(item, screenshots.get(item.key, ""), locale) for item in unmatched
    )
    strategy = _strategy_section(artifact.get("editorial_map", {}), recommendations, locale)
    director = _director_section(artifact.get("editorial_map", {}), recommendations, locale)
    coverage = _coverage_summary(artifact, locale)
    return f"""<!doctype html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(artifact['title_or_game'])} — {_tr(locale, 'Editorial map', '編集マップ')}</title>
<style>
:root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
body {{ margin: 0; background: #11151b; color: #e8edf3; }}
main {{ max-width: 1320px; margin: auto; padding: 32px 24px 72px; }}
h1, h2, h3 {{ line-height: 1.15; }} h1 {{ margin-bottom: 8px; }}
.subtitle, .muted, .empty {{ color: #9da8b5; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; margin: 24px 0; }}
.metric, .card {{ border: 1px solid #303946; border-radius: 12px; background: #181e26; padding: 16px; }}
.metric strong {{ display: block; margin-top: 6px; font-size: 1.15rem; }}
.sources, .cards {{ display: grid; gap: 12px; }}
.source-head, .recommendation-head {{ display: flex; gap: 12px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }}
.badge {{ border-radius: 999px; background: #293544; padding: 3px 9px; font-size: .78rem; text-transform: uppercase; }}
.editorial-card-body {{ display: grid; grid-template-columns: minmax(180px, 300px) minmax(0, 1fr); gap: 16px; margin-top: 12px; }}
.editorial-frame {{ width: 100%; aspect-ratio: 16 / 9; border-radius: 8px; background: #0b0e12; object-fit: contain; }}
.editorial-frame.placeholder {{ display: grid; place-items: center; color: #7f8b99; font-size: .82rem; }}
.directions {{ display: grid; gap: 12px; }}
.direction {{ border-left: 3px solid #667d99; padding-left: 10px; }} .direction strong {{ display: block; margin-bottom: 4px; }}
.narration {{ border-left: 4px solid #b786d9; }}
.editorial-pair {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr); gap: 18px; }}
.editorial-primary {{ min-width: 0; }}
.linked-editorial {{ min-width: 0; border-left: 1px solid #303946; padding-left: 18px; }}
.linked-editorial > h3 {{ margin-top: 0; font-size: 1rem; color: #b8c3d0; }}
.linked-stack {{ display: grid; gap: 10px; }}
.linked-card {{ border: 1px solid #303946; border-radius: 9px; padding: 12px; background: #141a21; }}
.linked-card h4 {{ margin: 0 0 6px; }}
.linked-card .editorial-frame {{ max-width: 220px; margin: 8px 0; }}
.linked-card ul {{ margin-bottom: 0; }}
.director-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
@media (max-width: 860px) {{ .editorial-pair, .director-grid {{ grid-template-columns: 1fr; }} .linked-editorial {{ border-left: 0; border-top: 1px solid #303946; padding: 16px 0 0; }} }}
@media (max-width: 720px) {{ .editorial-card-body {{ grid-template-columns: 1fr; }} }}
@media (prefers-color-scheme: light) {{ body {{ background:#f5f7fa; color:#18202a; }} .metric,.card,.linked-card {{ background:white; border-color:#d9e0e8; }} .muted,.empty,.subtitle {{ color:#596776; }} .badge {{ background:#e8eef5; }} }}
</style>
</head>
<body><main>
<header><h1>{_escape(artifact['title_or_game'])}</h1><p class="subtitle">{_escape(artifact['objective'])}</p></header>
<section class="summary">
  <div class="metric">{_tr(locale, 'Target duration', '目標時間')}<strong>{_duration_range(artifact['target_duration_min_ms'], artifact['target_duration_max_ms'], locale)}</strong></div>
  <div class="metric">{_tr(locale, 'Source duration', '素材時間')}<strong>{_duration(sum(item['duration_ms'] for item in sources), locale)}</strong></div>
  <div class="metric">{_tr(locale, 'Sources', '素材数')}<strong>{len(sources)}</strong></div>
  <div class="metric">{_tr(locale, 'Cumulative hosted API cost', 'ホスト API 累計費用')}<strong>${float(artifact.get('run_provenance', {}).get('actual_cost_usd', 0.0)):.2f}</strong></div>
  <div class="metric">{_tr(locale, 'Project status', 'プロジェクト状態')}<strong>{_escape(_status_label(artifact.get('editorial_map', {}).get('status', 'pending'), locale))}</strong></div>
  <div class="metric">{_tr(locale, 'Timeline assumption', 'タイムラインの前提')}<strong>{coverage}</strong></div>
</section>
<p class="muted">{_tr(locale, 'Timeline ranges without an editorial marker are intentionally treated as leave as-is.', '編集マーカーのないタイムライン区間は、意図的にそのまま残す扱いです。')}</p>
<section><h2>{_tr(locale, 'Sources', '素材')}</h2><div class="sources">{source_cards}</div></section>
{strategy}
{director}
<section><h2>{_tr(locale, 'Editorial recommendations', '編集提案')}</h2><div class="cards">{recommendation_cards}</div></section>
{f'<section><h2>{_tr(locale, "Standalone narration and creative notes", "独立したナレーション・演出メモ")}</h2><p class="muted">{_tr(locale, "These items did not overlap a specific recommendation and remain available for manual placement.", "特定の提案と重ならなかった項目です。手動配置用として利用できます。")}</p><div class="cards">{unmatched_cards}</div></section>' if unmatched_cards else ''}
</main></body></html>
"""


def _source_card(source: dict[str, Any], locale: str) -> str:
    completed = sum(stage.get("status") == "complete" for stage in source["stages"].values())
    media = (
        f"{_tr(locale, 'Gameplay/visual', 'ゲーム映像')}：{_escape(source['visual_original_name'])}<br>"
        f"{_tr(locale, 'Voice/facecam', '音声・フェイスカム')}：{_escape(source['audio_original_name'])}<br>"
        f"{_tr(locale, 'Pairing basis', 'ペア判定')}："
        f"{_escape(_pairing_basis_label(source['pairing_basis'], locale))}"
        if source["media_mode"] == "paired"
        else _tr(locale, "Standard single-file audio and visual analysis", "標準の単一ファイル音声・映像分析")
    )
    return f"""<article class="card">
<div class="source-head"><h3>{_escape(source['original_name'])}</h3><span class="badge">{_escape(_status_label(source['status'], locale))}</span></div>
<p class="muted">{media}<br>{_duration(source['duration_ms'], locale)} · {_tr(locale, f'{completed}/{len(source["stages"])} analysis stages complete', f'分析段階 {completed}/{len(source["stages"])} 完了')}</p>
</article>"""


def _recommendation_card(presented: PresentedEditorialItem, screenshot_url: str, locale: str) -> str:
    item = presented.item
    screenshot = _screenshot(screenshot_url, presented.label, locale)
    return f"""<article class="card">
<div class="recommendation-head"><h3>{_escape(presented.label)}</h3><span class="badge">{_escape(category_label(presented.category, locale))}</span></div>
<p class="muted">{_escape(presented.source.get('original_name', ''))} · {_timecode(item.get('start_ms', 0))}–{_timecode(item.get('end_ms', 0))}</p>
<div class="editorial-card-body">{screenshot}<div class="directions"><div class="direction"><strong>{_tr(locale, 'Editorial suggestion', '編集提案')}</strong>{_escape(primary_suggestion(presented, locale))}</div></div></div>
</article>"""


def _editorial_group_card(
    recommendation: PresentedEditorialItem,
    linked: list[PresentedEditorialItem],
    screenshots: dict[str, str],
    locale: str,
) -> str:
    item = recommendation.item
    screenshot = _screenshot(screenshots.get(recommendation.key, ""), recommendation.label, locale)
    linked_cards = "".join(
        _linked_item_card(value, screenshots.get(value.key, ""), locale) for value in linked
    ) or f'<p class="empty">{_tr(locale, "No narration or creative accent is linked to this direction.", "この提案に関連するナレーションや演出案はありません。")}</p>'
    return f"""<article class="card editorial-pair">
<div class="editorial-primary"><div class="recommendation-head"><h3>{_escape(recommendation.label)}</h3><span class="badge">{_escape(category_label(recommendation.category, locale))}</span></div>
<p class="muted">{_escape(recommendation.source.get('original_name', ''))} · {_timecode(item.get('start_ms', 0))}–{_timecode(item.get('end_ms', 0))}</p>
<div class="editorial-card-body">{screenshot}<div class="directions"><div class="direction"><strong>{_tr(locale, 'Editorial suggestion', '編集提案')}</strong>{_escape(primary_suggestion(recommendation, locale))}</div></div></div></div>
<aside class="linked-editorial"><h3>{_tr(locale, 'Linked narration and accents', '関連するナレーション・演出')}</h3><div class="linked-stack">{linked_cards}</div></aside>
</article>"""


def _linked_item_card(presented: PresentedEditorialItem, screenshot_url: str, locale: str) -> str:
    item = presented.item
    screenshot = _screenshot(screenshot_url, presented.label, locale)
    timing = f"{_timecode(item.get('start_ms', 0))}–{_timecode(item.get('end_ms', 0))}"
    if presented.kind == "narration":
        points = item.get("talking_points") if isinstance(item.get("talking_points"), list) else []
        bullets = "".join(f"<li>{_escape(point)}</li>" for point in points)
        return f"""<section class="linked-card narration"><div class="recommendation-head"><h4>{_tr(locale, 'Narration', 'ナレーション')} · {_escape(presented.label)}</h4><span class="badge">{timing}</span></div>{screenshot}<p><strong>{_escape(item.get('purpose') or _tr(locale, 'Narration opportunity', 'ナレーション候補'))}</strong></p><p>{_escape(item.get('memory_jog', ''))}</p>{f'<ul>{bullets}</ul>' if bullets else ''}</section>"""
    return f"""<section class="linked-card"><div class="recommendation-head"><h4>{_tr(locale, 'Creative accent', '演出案')} · {_escape(presented.label)}</h4><span class="badge">{timing}</span></div>{screenshot}<div class="directions"><div class="direction"><strong>{_tr(locale, 'Suggestion', '提案')}</strong>{_escape(primary_suggestion(presented, locale))}</div></div></section>"""


def _standalone_linked_card(
    presented: PresentedEditorialItem, screenshot_url: str, locale: str
) -> str:
    return (
        _narration_card(presented, screenshot_url, locale)
        if presented.kind == "narration"
        else _recommendation_card(presented, screenshot_url, locale)
    )


def _narration_card(presented: PresentedEditorialItem, screenshot_url: str, locale: str) -> str:
    item = presented.item
    points = item.get("talking_points") if isinstance(item.get("talking_points"), list) else []
    bullets = "".join(f"<li>{_escape(point)}</li>" for point in points)
    screenshot = _screenshot(screenshot_url, presented.label, locale)
    return f"""<article class="card narration"><div class="recommendation-head"><h3>{_escape(presented.label)} — {_escape(item.get('purpose') or _tr(locale, 'Narration opportunity', 'ナレーション候補'))}</h3><span class="badge">{_timecode(item.get('start_ms', 0))}–{_timecode(item.get('end_ms', 0))}</span></div><p class="muted">{_escape(presented.source.get('original_name', ''))}</p><div class="editorial-card-body">{screenshot}<div><p>{_escape(item.get('memory_jog', ''))}</p><ul>{bullets}</ul></div></div></article>"""


def _group_linked_editorial_items(
    presented: list[PresentedEditorialItem],
) -> tuple[
    list[tuple[PresentedEditorialItem, list[PresentedEditorialItem]]],
    list[PresentedEditorialItem],
]:
    recommendations = [item for item in presented if item.kind == "recommendation"]
    linked_by_key: dict[str, list[PresentedEditorialItem]] = {
        item.key: [] for item in recommendations
    }
    by_id = {
        str(item.item.get("id")): item
        for item in recommendations
        if item.item.get("id")
    }
    unmatched: list[PresentedEditorialItem] = []
    for candidate in presented:
        if candidate.kind == "recommendation":
            continue
        explicit = str(
            candidate.item.get("recommendation_id")
            or candidate.item.get("parent_recommendation_id")
            or ""
        )
        matched = by_id.get(explicit) if explicit else None
        if matched is None:
            options = [
                item
                for item in recommendations
                if item.item.get("source_id") == candidate.item.get("source_id")
                and _overlap_ms(item.item, candidate.item) > 0
            ]
            if options:
                matched = max(
                    options,
                    key=lambda item: (
                        _overlap_ms(item.item, candidate.item),
                        -abs(_midpoint_ms(item.item) - _midpoint_ms(candidate.item)),
                    ),
                )
        if matched is None:
            unmatched.append(candidate)
        else:
            linked_by_key[matched.key].append(candidate)
    grouped = []
    for recommendation in recommendations:
        linked = sorted(
            linked_by_key[recommendation.key],
            key=lambda item: (
                int(item.item.get("start_ms", 0)),
                0 if item.kind == "narration" else 1,
            ),
        )
        grouped.append((recommendation, linked))
    return grouped, unmatched


def _overlap_ms(left: dict[str, Any], right: dict[str, Any]) -> int:
    try:
        return max(
            0,
            min(int(left.get("end_ms", 0)), int(right.get("end_ms", 0)))
            - max(int(left.get("start_ms", 0)), int(right.get("start_ms", 0))),
        )
    except (TypeError, ValueError):
        return 0


def _midpoint_ms(item: dict[str, Any]) -> int:
    try:
        return (int(item.get("start_ms", 0)) + int(item.get("end_ms", 0))) // 2
    except (TypeError, ValueError):
        return 0


def _director_section(editorial_map: dict[str, Any], recommendations: list[Any], locale: str) -> str:
    review = editorial_map.get("director_review")
    if not isinstance(review, dict):
        return ""
    assessments = [
        (_tr(locale, "Pacing", "テンポ"), review.get("pacing_assessment")),
        (_tr(locale, "Intrigue", "引き"), review.get("intrigue_assessment")),
        (_tr(locale, "Information density", "情報密度"), review.get("information_density_assessment")),
        (_tr(locale, "Continuity", "連続性"), review.get("continuity_assessment")),
    ]
    assessment_html = "".join(
        f'<div class="direction"><strong>{_escape(label)}</strong>{_escape(value)}</div>'
        for label, value in assessments
        if str(value or "").strip()
    )
    by_id = {
        str(item.get("id")): item
        for item in recommendations
        if isinstance(item, dict) and item.get("id")
    }
    priorities = []
    for change in review.get("priority_changes", []):
        if not isinstance(change, dict):
            continue
        recommendation = by_id.get(str(change.get("recommendation_id") or ""), {})
        subject = str(
            recommendation.get("reason")
            or change.get("recommendation_id")
            or _tr(locale, "Editorial direction", "編集方針")
        ).rstrip(" .:;")
        priorities.append(
            f"<li><strong>{_escape(subject)}</strong>: {_escape(change.get('action'))} — {_escape(change.get('rationale'))}</li>"
        )
    protected = []
    for moment in review.get("protected_moments", []):
        if not isinstance(moment, dict):
            continue
        recommendation = by_id.get(str(moment.get("recommendation_id") or ""), {})
        subject = str(
            recommendation.get("reason")
            or moment.get("recommendation_id")
            or _tr(locale, "Editorial moment", "編集上の重要場面")
        ).rstrip(" .:;")
        protected.append(
            f"<li><strong>{_escape(subject)}</strong>: {_escape(moment.get('rationale'))}</li>"
        )
    questions = "".join(
        f"<li>{_escape(value)}</li>"
        for value in review.get("unresolved_questions", [])
        if str(value or "").strip()
    )
    return f"""<section><h2>{_tr(locale, 'Director’s review', 'ディレクター総評')}</h2><article class="card"><p>{_escape(review.get('executive_direction', ''))}</p><div class="director-grid">{assessment_html}</div>{f'<h3>{_tr(locale, "Highest-priority changes", "最優先の修正")}</h3><ol>{"".join(priorities)}</ol>' if priorities else ''}{f'<h3>{_tr(locale, "Moments to protect", "守るべき場面")}</h3><ul>{"".join(protected)}</ul>' if protected else ''}{f'<h3>{_tr(locale, "Questions for the editor", "編集者への確認事項")}</h3><ul>{questions}</ul>' if questions else ''}</article></section>"""


def _strategy_section(editorial_map: dict[str, Any], recommendations: list[Any], locale: str) -> str:
    direction = str(editorial_map.get("editorial_direction_summary") or "").strip()
    budget = editorial_map.get("duration_budget") if isinstance(editorial_map.get("duration_budget"), dict) else {}
    by_id = {
        str(item.get("id")): item
        for item in recommendations
        if isinstance(item, dict) and item.get("id")
    }
    plan = _strategy_plan(editorial_map.get("optimal_plan"), by_id, locale)
    if not direction and not plan:
        return f'<section><h2>{_tr(locale, "Editorial direction", "編集方針")}</h2><p class="empty">{_tr(locale, "Global reconciliation has not run yet.", "全体調整はまだ実行されていません。")}</p></section>'
    warning = str(budget.get("warning") or "").strip()
    no_changes = _tr(locale, "No additional changes proposed.", "追加の変更提案はありません。")
    estimate = _estimated_duration_note(budget, locale)
    return f"""<section><h2>{_tr(locale, 'Overall direction', '全体方針')}</h2><article class="card"><p>{_escape(direction)}</p>{estimate}{f'<p class="muted">{_escape(warning)}</p>' if warning else ''}<div class="directions"><div class="direction"><strong>{_tr(locale, 'Selected editorial plan', '選択された編集方針')}</strong>{plan or no_changes}</div></div></article></section>"""


def _strategy_plan(value: Any, by_id: dict[str, dict[str, Any]], locale: str) -> str:
    if not isinstance(value, list):
        return ""
    items = []
    for decision in value:
        if not isinstance(decision, dict):
            continue
        recommendation = by_id.get(str(decision.get("recommendation_id") or ""))
        if recommendation is None:
            continue
        label = recommendation.get("reason") or decision.get("reason") or decision.get("recommendation_id")
        kept_ms = _integer(decision.get("selected_kept_ms"))
        kept = _duration(kept_ms, locale)
        target = (
            f' <span class="muted">({_tr(locale, "keep about", "残す長さの目安")} '
            f"{_escape(kept)})</span>"
            if kept_ms > 0
            else ""
        )
        items.append(
            f"<li>{_escape(label)}{target}</li>"
        )
    return f"<ol>{''.join(items)}</ol>" if items else ""


def _estimated_duration_note(budget: dict[str, Any], locale: str) -> str:
    estimated = _integer(budget.get("estimated_final_ms"))
    if estimated <= 0:
        return ""
    return (
        f'<p class="muted">{_tr(locale, "Estimated final duration", "完成尺の目安")}: '
        f"{_escape(_duration(estimated, locale))}</p>"
    )


def _write_editorial_screenshots(
    report_path: Path, items: list[PresentedEditorialItem]
) -> dict[str, str]:
    if not items:
        return {}
    directory = report_path.with_name(f"{report_path.stem}-frames")
    urls: dict[str, str] = {}
    for presented in items:
        source_path = Path(str(presented.source.get("visual_path") or ""))
        if not source_path.is_file():
            continue
        safe_label = re.sub(r"[^\w.-]+", "_", presented.label, flags=re.UNICODE).strip("._")
        target = directory / f"{safe_label or 'marker'}.jpg"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.stat().st_size == 0:
                timestamp = max(0.0, float(presented.item.get("start_ms", 0)) / 1000.0)
                completed = subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{timestamp:.3f}",
                        "-i",
                        str(source_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=640:-2:force_original_aspect_ratio=decrease",
                        "-q:v",
                        "4",
                        "-y",
                        str(target),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
                if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                    continue
            urls[presented.key] = f"{directory.name}/{target.name}"
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            continue
    return urls


def _screenshot(url: str, label: str, locale: str) -> str:
    if not url:
        return f'<div class="editorial-frame placeholder">{_tr(locale, "Preview unavailable", "プレビューなし")}</div>'
    return f'<img class="editorial-frame" src="{_escape(url)}" alt="{_tr(locale, "First frame for", "先頭フレーム")} {_escape(label)}">'


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _duration_range(minimum_ms: int, maximum_ms: int, locale: str = "en") -> str:
    minimum = _duration(minimum_ms, locale)
    maximum = _duration(maximum_ms, locale)
    return minimum if minimum_ms == maximum_ms else f"{minimum}–{maximum}"


def _duration(milliseconds: int, locale: str = "en") -> str:
    total_minutes = max(0, int(round(milliseconds / 60_000)))
    hours, minutes = divmod(total_minutes, 60)
    if locale == "ja":
        return f"{hours}時間{minutes:02d}分" if hours else f"{minutes}分"
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _timecode(value: Any) -> str:
    try:
        total_seconds = max(0, int(value) // 1000)
    except (TypeError, ValueError):
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _integer(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _coverage_summary(artifact: dict[str, Any], locale: str = "en") -> str:
    values = artifact.get("editorial_map", {}).get("timeline_coverage", [])
    if not isinstance(values, list):
        return _tr(locale, "Pending", "未処理")
    suggested = sum(
        max(0, int(item.get("end_ms", 0)) - int(item.get("start_ms", 0)))
        for item in values
        if isinstance(item, dict) and item.get("status") == "suggested"
    )
    total = sum(int(source.get("duration_ms", 0)) for source in artifact.get("sources", []))
    if total <= 0:
        return _tr(locale, "Pending", "未処理")
    percentage = f"{suggested / total:.0%}"
    return _tr(locale, f"{percentage} has guidance", f"{percentage} に提案あり")


def _tr(locale: str, english: str, japanese: str) -> str:
    return locale_label(locale, english, japanese)


def _status_label(value: Any, locale: str) -> str:
    status = str(value or "pending")
    labels = {
        "pending": ("Pending", "未処理"),
        "in_progress": ("In progress", "処理中"),
        "complete": ("Complete", "完了"),
        "failed": ("Failed", "失敗"),
    }
    english, japanese = labels.get(status, (status, status))
    return _tr(locale, english, japanese)


def _pairing_basis_label(value: Any, locale: str) -> str:
    basis = str(value or "single")
    labels = {
        "single": ("Single file", "単一ファイル"),
        "filename": ("Filename", "ファイル名"),
        "resolution": ("Resolution", "解像度"),
        "manual": ("Manual selection", "手動指定"),
    }
    english, japanese = labels.get(basis, (basis, basis))
    return _tr(locale, english, japanese)
