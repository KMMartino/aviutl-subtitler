"""Human-facing labels and concise directions for editorial artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .editorial_locale import locale_label


EditorialItemKind = Literal["recommendation", "narration", "creative"]
EDITORIAL_LAYER_ORDER = ("cut", "condense", "voiceover", "keep", "connect_review", "creative")


@dataclass(frozen=True)
class PresentedEditorialItem:
    key: str
    kind: EditorialItemKind
    label: str
    category: str
    source: dict[str, Any]
    item: dict[str, Any]


def presented_editorial_items(artifact: dict[str, Any]) -> list[PresentedEditorialItem]:
    sources = sorted(artifact.get("sources", []), key=lambda item: item.get("order", 0))
    source_by_id = {
        str(source.get("source_id")): source
        for source in sources
        if isinstance(source, dict) and source.get("source_id")
    }
    source_order = {
        source_id: int(source.get("order", 0)) for source_id, source in source_by_id.items()
    }
    raw_items: list[tuple[EditorialItemKind, int, dict[str, Any]]] = []
    editorial_map = artifact.get("editorial_map", {})
    selected_recommendations = _selected_recommendation_plan(editorial_map)
    for kind, field in (
        ("recommendation", "recommendations"),
        ("narration", "narration_briefs"),
        ("creative", "creative_suggestions"),
    ):
        values = editorial_map.get(field, []) if isinstance(editorial_map, dict) else []
        for index, item in enumerate(values if isinstance(values, list) else []):
            if (
                isinstance(item, dict)
                and str(item.get("source_id")) in source_by_id
                and (kind != "recommendation" or selected_recommendations is None or str(item.get("id")) in selected_recommendations)
            ):
                normalized = dict(item)
                if kind == "recommendation" and selected_recommendations is not None:
                    normalized["selected_kept_ms"] = selected_recommendations[
                        str(item.get("id"))
                    ].get("selected_kept_ms", 0)
                raw_items.append((kind, index, normalized))
    raw_items.sort(
        key=lambda value: (
            source_order.get(str(value[2].get("source_id")), 0),
            _integer(value[2].get("start_ms")),
            _integer(value[2].get("end_ms")),
            {"recommendation": 0, "narration": 1, "creative": 2}[value[0]],
            value[1],
        )
    )

    counters: dict[str, int] = {}
    result: list[PresentedEditorialItem] = []
    for kind, original_index, item in raw_items:
        source = source_by_id[str(item["source_id"])]
        stem = Path(str(source.get("original_name") or "recording")).stem or "recording"
        counter_key = stem.casefold()
        counters[counter_key] = counters.get(counter_key, 0) + 1
        label = f"{stem}-{counters[counter_key]:03d}"
        internal_id = str(item.get("id") or original_index)
        result.append(
            PresentedEditorialItem(
                key=f"{kind}:{internal_id}",
                kind=kind,
                label=label,
                category=editorial_category(kind, item),
                source=source,
                item=item,
            )
        )
    return result


def _selected_recommendation_plan(editorial_map: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(editorial_map, dict):
        return None
    checkpoint = editorial_map.get("global_reconciliation")
    if not isinstance(checkpoint, dict) or checkpoint.get("status") != "complete":
        return None
    plan = editorial_map.get("optimal_plan")
    if not isinstance(plan, list):
        return None
    return {
        str(item.get("recommendation_id")): item
        for item in plan
        if isinstance(item, dict) and str(item.get("recommendation_id") or "")
    }


def editorial_category(kind: EditorialItemKind, item: dict[str, Any]) -> str:
    if kind == "creative":
        return "creative"
    if kind == "narration" or str(item.get("presentation_mode") or "").startswith("narration_"):
        return "voiceover"
    disposition = str(item.get("disposition") or "review")
    if disposition == "omit":
        return "cut"
    if disposition == "condense":
        return "condense"
    if disposition == "keep":
        return "keep"
    return "connect_review"


def category_label(category: str, locale: str = "en") -> str:
    labels = {
        "cut": ("CUT", "カット"),
        "condense": ("CONDENSE", "短縮"),
        "voiceover": ("MONTAGE + VOICEOVER", "モンタージュ＋ナレーション"),
        "keep": ("KEEP", "維持"),
        "connect_review": ("CONNECT / REVIEW", "接続・要確認"),
        "creative": ("CREATIVE EDIT", "演出案"),
    }
    english, japanese = labels.get(category, ("REVIEW", "要確認"))
    return locale_label(locale, english, japanese)


def primary_suggestion(presented: PresentedEditorialItem, locale: str = "en") -> str:
    item = presented.item
    if presented.kind == "creative":
        suggestion = _first_text(item.get("suggestion"), locale_label(locale, "Add a restrained editorial accent here.", "ここに控えめな編集演出を加えます。"))
        trigger = _first_text(item.get("trigger"))
        trigger_label = locale_label(locale, "Trigger", "きっかけ")
        return f"{suggestion} {trigger_label}: {trigger}" if trigger else suggestion
    if presented.kind == "narration":
        return _first_text(item.get("purpose"), item.get("memory_jog"), locale_label(locale, "Record a narration bridge.", "つなぎのナレーションを収録します。"))
    action = {
        "cut": locale_label(locale, "Cut this section.", "この区間をカットします。"),
        "condense": locale_label(locale, "Condense this section to its strongest moments.", "この区間を最も強い場面に絞ります。"),
        "voiceover": locale_label(locale, "Replace most of this section with concise narration and representative footage.", "この区間の大半を簡潔なナレーションと代表映像に置き換えます。"),
        "keep": locale_label(locale, "Keep this section substantially intact.", "この区間はほぼそのまま残します。"),
        "connect_review": locale_label(locale, "Connect this section to the related material before making the cut.", "カットを決める前に関連箇所とのつながりを確認します。"),
    }.get(presented.category, locale_label(locale, "Review this section before cutting.", "カット前にこの区間を確認します。"))
    reason = _first_text(item.get("reason"), item.get("viewer_benefit"))
    duration = _selected_duration(item.get("selected_kept_ms"), locale)
    return f"{action} {reason} {duration}".strip()


def _selected_duration(value: Any, locale: str) -> str:
    milliseconds = _integer(value)
    if milliseconds <= 0:
        return ""
    total_seconds = max(1, round(milliseconds / 1000))
    minutes, seconds = divmod(total_seconds, 60)
    if locale == "ja":
        duration = f"{minutes}分{seconds}秒" if minutes else f"{seconds}秒"
        return f"残す長さの目安: {duration}。"
    duration = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    return f"Keep about {duration}."


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
