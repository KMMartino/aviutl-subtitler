"""Human-readable report generation for suggestion-only editorial artifacts."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .editorial_presentation import (
    PresentedEditorialItem,
    category_label,
    presented_editorial_items,
    primary_suggestion,
)
from .editorial_actions import action_color
from .editorial_locale import editorial_locale, locale_label
from .editorial_project import validate_editorial_project


def write_editorial_html(path: Path, artifact: dict[str, Any]) -> None:
    validate_editorial_project(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    presented = presented_editorial_items(artifact)
    screenshots = _write_editorial_screenshots(path, presented, artifact)
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
    source_offsets = _source_offsets(sources)
    total_duration = sum(int(item.get("duration_ms", 0)) for item in sources)
    narration_by_id = {
        str(item.get("id")): item
        for item in artifact.get("editorial_map", {}).get("narration_briefs", [])
        if isinstance(item, dict) and item.get("id")
    }
    threads_by_id = {
        str(item.get("thread_id")): item
        for item in artifact.get("editorial_map", {}).get("editorial_threads", [])
        if isinstance(item, dict) and item.get("thread_id")
    }
    assets_by_id = {
        str(item.get("asset_id")): item
        for item in artifact.get("editorial_map", {}).get("assets", [])
        if isinstance(item, dict) and item.get("asset_id")
    }
    recommendation_cards = "".join(
        _editorial_group_card(
            recommendation,
            linked,
            screenshots,
            locale,
            source_offsets,
            total_duration,
            narration_by_id,
            threads_by_id,
            assets_by_id,
        )
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
:root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; font-size: 18px; }}
body {{ margin: 0; background: #11151b; color: #e8edf3; line-height: 1.55; }}
main {{ max-width: 1480px; margin: auto; padding: 36px 28px 80px; }}
h1, h2, h3, h4 {{ line-height: 1.2; }} h1 {{ margin-bottom: 8px; font-size: 2.35rem; }} h2 {{ font-size: 1.65rem; }} h3 {{ font-size: 1.25rem; }} h4 {{ font-size: 1.08rem; }}
.subtitle, .muted, .empty {{ color: #9da8b5; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; margin: 24px 0; }}
.metric, .card {{ border: 1px solid #303946; border-radius: 12px; background: #181e26; padding: 20px; }}
.metric strong {{ display: block; margin-top: 6px; font-size: 1.15rem; }}
.sources, .cards {{ display: grid; gap: 12px; }}
.source-head, .recommendation-head {{ display: flex; gap: 12px; align-items: baseline; justify-content: space-between; flex-wrap: wrap; }}
.badge {{ border-radius: 999px; background: #293544; padding: 4px 10px; font-size: .82rem; text-transform: uppercase; }}
.editorial-card-body {{ display: grid; grid-template-columns: minmax(180px, 300px) minmax(0, 1fr); gap: 16px; margin-top: 12px; }}
.editorial-frame {{ width: 100%; aspect-ratio: 16 / 9; border-radius: 8px; background: #0b0e12; object-fit: contain; }}
.editorial-frame.placeholder {{ display: grid; place-items: center; color: #7f8b99; font-size: .82rem; }}
.directions {{ display: grid; gap: 12px; }}
.direction {{ border-left: 3px solid #667d99; padding-left: 10px; }} .direction strong {{ display: block; margin-bottom: 4px; }}
.narration {{ border-left: 4px solid #b786d9; }}
.editorial-pair {{ display: grid; grid-template-columns: 36px minmax(0, 1fr) minmax(0, 1fr); column-gap: 16px; row-gap: 24px; padding-left: 14px; border-left: 5px solid var(--action-color, #667d99); }}
.timeline-rail {{ position: relative; z-index: 2; width: 108px; min-height: 260px; display: flex; flex-direction: column; justify-content: space-between; color: #aeb9c6; font-variant-numeric: tabular-nums; font-size: .82rem; pointer-events: none; }}
.timeline-rail::before {{ content: ""; position: absolute; top: 28px; bottom: 28px; left: 12px; width: 3px; border-radius: 2px; background: #303946; }}
.timeline-progress {{ position: absolute; left: 12px; width: 3px; min-height: 4px; border-radius: 2px; background: var(--action-color, #667d99); top: var(--start); height: max(4px, calc(var(--end) - var(--start))); }}
.timeline-rail span {{ padding-left: 24px; position: relative; z-index: 1; white-space: nowrap; }}
.editorial-primary {{ min-width: 0; padding-bottom: 72px; }}
.editorial-primary > .recommendation-head {{ margin-left: 16px; }}
.editorial-primary .editorial-card-body {{ grid-template-columns: minmax(0, 1fr); gap: 14px; }}
.linked-editorial {{ min-width: 0; border-left: 1px solid #303946; padding-left: 24px; }}
.linked-editorial > h3 {{ margin-top: 0; font-size: 1.15rem; color: #b8c3d0; }}
.linked-stack {{ display: grid; gap: 14px; }}
.linked-card {{ border: 1px solid #303946; border-radius: 9px; padding: 16px; background: #141a21; }}
.linked-card h4 {{ margin: 0 0 6px; }}
.linked-card .editorial-frame {{ max-width: 480px; margin: 12px 0; }}
.linked-card ul {{ margin-bottom: 0; }}
.thread-chips {{ display: flex; flex-wrap: wrap; gap: 7px; margin: 9px 0; }}
.thread-chip {{ border: 1px solid var(--thread-color); border-left-width: 7px; border-radius: 999px; padding: 2px 9px; font-size: .78rem; color: #d7e0ea; }}
.narration-brief {{ border-left: 4px solid #bf83ff; margin-top: 14px; padding: 12px 14px; background: #141a21; border-radius: 7px; }}
.reference-proof {{ margin-top: 12px; padding-top: 12px; border-top: 1px solid #303946; }}
.reference-proof img {{ width: 100%; max-width: 620px; border-radius: 8px; background: #0b0e12; }}
.verification {{ font-size: .82rem; color: #9da8b5; }}
.director-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
@media (max-width: 1100px) {{ .editorial-pair {{ grid-template-columns: 36px minmax(0, 1fr); }} .linked-editorial {{ grid-column: 2; border-left: 0; border-top: 1px solid #303946; padding: 20px 0 0; }} .director-grid {{ grid-template-columns: 1fr; }} }}
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
<p class="muted">{_tr(locale, 'The final plan covers the complete timeline. Preserve means leave that recorded section unchanged.', '最終プランはタイムライン全体を対象にしています。「維持」は、その収録区間を変更せず残す意味です。')}</p>
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
    source_offsets: dict[str, int],
    total_duration: int,
    narration_by_id: dict[str, dict[str, Any]],
    threads_by_id: dict[str, dict[str, Any]],
    assets_by_id: dict[str, dict[str, Any]],
) -> str:
    item = recommendation.item
    screenshot = _screenshot(screenshots.get(recommendation.key, ""), recommendation.label, locale)
    linked_cards = "".join(
        _linked_item_card(
            value,
            screenshots.get(value.key, ""),
            locale,
            threads_by_id,
            assets_by_id,
            screenshots,
        ) for value in linked
    ) or f'<p class="empty">{_tr(locale, "No supporting edit is needed here.", "ここには追加の演出は必要ありません。")}</p>'
    direction_number = _integer(item.get("direction_number"))
    number = f"{direction_number}. " if direction_number else ""
    thread_chips = _thread_chips(item.get("thread_ids"), threads_by_id)
    narration = _narration_briefs(item, narration_by_id, locale)
    source_id = str(item.get("source_id") or "")
    absolute_start = source_offsets.get(source_id, 0) + _integer(item.get("start_ms"))
    absolute_end = source_offsets.get(source_id, 0) + _integer(item.get("end_ms"))
    start_percent = absolute_start / max(1, total_duration) * 100
    end_percent = absolute_end / max(1, total_duration) * 100
    action_style = action_color(item.get("action_type"))
    return f"""<article id="direction-{direction_number or recommendation.label}" class="card editorial-pair" style="--action-color:{action_style}">
<div class="timeline-rail"><span>{_timecode(absolute_start)}</span><i class="timeline-progress" style="--start:{start_percent:.3f}%;--end:{end_percent:.3f}%"></i><span>{_timecode(absolute_end)}</span></div>
<div class="editorial-primary"><div class="recommendation-head"><h3>{number}{_escape(recommendation.label)}</h3><span class="badge">{_escape(category_label(recommendation.category, locale))}</span></div>
<p class="muted">{_escape(recommendation.source.get('original_name', ''))} · {_timecode(item.get('start_ms', 0))}–{_timecode(item.get('end_ms', 0))}</p>
{thread_chips}<div class="editorial-card-body">{screenshot}<div class="directions"><div class="direction"><strong>{_tr(locale, 'Editorial suggestion', '編集提案')}</strong>{_escape(primary_suggestion(recommendation, locale))}</div>{_rationale(item, locale)}</div></div>{narration}</div>
<aside class="linked-editorial"><h3>{_tr(locale, 'Suggested edits', '追加の編集案')}</h3><div class="linked-stack">{linked_cards}</div></aside>
</article>"""


def _linked_item_card(
    presented: PresentedEditorialItem,
    screenshot_url: str,
    locale: str,
    threads_by_id: dict[str, dict[str, Any]],
    assets_by_id: dict[str, dict[str, Any]],
    screenshots: dict[str, str],
) -> str:
    item = presented.item
    screenshot = _screenshot(screenshot_url, presented.label, locale)
    timing = f"{_timecode(item.get('start_ms', 0))}–{_timecode(item.get('end_ms', 0))}"
    if presented.kind == "narration":
        points = item.get("talking_points") if isinstance(item.get("talking_points"), list) else []
        bullets = "".join(f"<li>{_escape(point)}</li>" for point in points)
        return f"""<section class="linked-card narration"><div class="recommendation-head"><h4>{_tr(locale, 'Narration', 'ナレーション')} · {_escape(presented.label)}</h4><span class="badge">{timing}</span></div>{screenshot}<p><strong>{_escape(item.get('purpose') or _tr(locale, 'Narration opportunity', 'ナレーション候補'))}</strong></p><p>{_escape(item.get('memory_jog', ''))}</p>{f'<ul>{bullets}</ul>' if bullets else ''}</section>"""
    asset = assets_by_id.get(str(item.get("resolved_asset_id") or ""))
    proof = _asset_proof(asset, screenshots, locale) if asset else ""
    return f"""<section class="linked-card"><div class="recommendation-head"><h4>{_escape(category_label(str(item.get('action_type') or 'creative'), locale))} · {_escape(presented.label)}</h4><span class="badge">{timing}</span></div>{_thread_chips(item.get('thread_ids'), threads_by_id)}{screenshot}<div class="directions"><div class="direction"><strong>{_tr(locale, 'Suggestion', '提案')}</strong>{_escape(primary_suggestion(presented, locale))}</div>{_rationale(item, locale)}</div>{proof}</section>"""


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


_THREAD_COLORS = (
    "#50c9a7", "#f28b82", "#8ab4f8", "#fbbc04", "#c58af9",
    "#7bdff2", "#ff9f68", "#9ad27a", "#e38aa8", "#85a6ff",
)


def _source_offsets(sources: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    offset = 0
    for source in sources:
        result[str(source.get("source_id") or "")] = offset
        offset += int(source.get("duration_ms", 0))
    return result


def _thread_chips(value: Any, threads_by_id: dict[str, dict[str, Any]]) -> str:
    if not isinstance(value, list):
        return ""
    chips = []
    for thread_id in value:
        thread = threads_by_id.get(str(thread_id))
        if thread is None:
            continue
        try:
            number = max(0, int(str(thread_id).rsplit("-", 1)[-1]) - 1)
        except ValueError:
            number = sum(ord(char) for char in str(thread_id))
        color = _THREAD_COLORS[number % len(_THREAD_COLORS)]
        title = thread.get("title") or thread_id
        chips.append(
            f'<span class="thread-chip" style="--thread-color:{color}" title="{_escape(thread.get("editorial_use"))}">{_escape(title)}</span>'
        )
    return f'<div class="thread-chips">{"".join(chips)}</div>' if chips else ""


def _narration_briefs(
    item: dict[str, Any], narration_by_id: dict[str, dict[str, Any]], locale: str
) -> str:
    ids = item.get("narration_brief_ids")
    if not isinstance(ids, list):
        return ""
    blocks = []
    for narration_id in ids:
        brief = narration_by_id.get(str(narration_id))
        if brief is None:
            continue
        points = brief.get("talking_points") if isinstance(brief.get("talking_points"), list) else []
        bullets = "".join(f"<li>{_escape(point)}</li>" for point in points[:10])
        blocks.append(
            f'<div class="narration-brief"><strong>{_tr(locale, "Narration brief", "ナレーション案")}: '
            f'{_escape(brief.get("purpose"))}</strong><p>{_escape(brief.get("memory_jog"))}</p>'
            f'{f"<ul>{bullets}</ul>" if bullets else ""}</div>'
        )
    return "".join(blocks)


def _rationale(item: dict[str, Any], locale: str) -> str:
    rationale = str(item.get("rationale") or "").strip()
    if not rationale:
        return ""
    return f'<div class="direction"><strong>{_tr(locale, "Why", "理由")}</strong>{_escape(rationale)}</div>'


def _asset_proof(
    asset: dict[str, Any], screenshots: dict[str, str], locale: str
) -> str:
    url = screenshots.get(f"asset:{asset.get('asset_id')}", "")
    image = f'<img src="{_escape(url)}" alt="{_escape(asset.get("caption"))}">' if url else ""
    status = _tr(locale, "Verified source frame", "確認済みの素材フレーム") if asset.get("verified") else _tr(locale, "Unverified candidate", "未確認の候補")
    return f'<div class="reference-proof">{image}<p><strong>{_escape(status)}</strong> · {_timecode(asset.get("timestamp_ms"))}</p><p>{_escape(asset.get("caption"))}</p><p class="verification">{_escape(asset.get("verification_note"))}</p></div>'


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
    questions = "".join(
        f"<li>{_escape(value)}</li>"
        for value in review.get("unresolved_questions", [])
        if str(value or "").strip()
    )
    return f"""<section><h2>{_tr(locale, 'Director’s review', 'ディレクター総評')}</h2><article class="card"><p>{_escape(review.get('executive_direction', ''))}</p><div class="director-grid">{assessment_html}</div>{f'<h3>{_tr(locale, "Questions for the editor", "編集者への確認事項")}</h3><ul>{questions}</ul>' if questions else ''}</article></section>"""


def _strategy_section(editorial_map: dict[str, Any], recommendations: list[Any], locale: str) -> str:
    direction = str(editorial_map.get("editorial_direction_summary") or "").strip()
    budget = editorial_map.get("duration_budget") if isinstance(editorial_map.get("duration_budget"), dict) else {}
    by_id = {
        str(item.get("id")): item
        for item in recommendations
        if isinstance(item, dict) and item.get("id")
    }
    final_actions = editorial_map.get("final_actions")
    plan = (
        _final_strategy_plan(final_actions, locale)
        if isinstance(final_actions, list) and final_actions
        else _strategy_plan(editorial_map.get("optimal_plan"), by_id, locale)
    )
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
        items.append(f"<li>{_escape(label)}</li>")
    return f"<ol>{''.join(items)}</ol>" if items else ""


def _final_strategy_plan(value: list[Any], locale: str) -> str:
    items = []
    for index, action in enumerate(value, 1):
        if not isinstance(action, dict):
            continue
        instruction = str(action.get("instruction") or "").strip()
        if not instruction:
            continue
        items.append(
            f'<li><a href="#direction-{index}">{_escape(instruction)}</a></li>'
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
    report_path: Path, items: list[PresentedEditorialItem], artifact: dict[str, Any]
) -> dict[str, str]:
    assets = artifact.get("editorial_map", {}).get("assets", [])
    if not items and not assets:
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
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or not asset.get("asset_id"):
            continue
        source = Path(str(asset.get("path") or ""))
        if not source.is_file():
            continue
        target = directory / f"{asset['asset_id']}.jpg"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            urls[f"asset:{asset['asset_id']}"] = f"{directory.name}/{target.name}"
        except OSError:
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
    final_actions = artifact.get("editorial_map", {}).get("final_actions", [])
    if isinstance(final_actions, list) and final_actions:
        return _tr(locale, "Complete action coverage", "全区間に方針あり")
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
