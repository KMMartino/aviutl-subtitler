"""Human-facing labels and concise directions for editorial artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .editorial_actions import EDITORIAL_ACTION_SPECS, action_label
from .editorial_locale import locale_label


EditorialItemKind = Literal["recommendation", "narration", "creative"]
EDITORIAL_LAYER_ORDER = tuple(spec.action_type for spec in EDITORIAL_ACTION_SPECS) + (
    "condense",
    "voiceover",
    "keep",
    "connect_review",
    "creative",
)


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
    final_actions = editorial_map.get("final_actions") if isinstance(editorial_map, dict) else None
    if isinstance(final_actions, list) and final_actions:
        return _presented_final_actions(final_actions, editorial_map, source_by_id, source_order)
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


def _presented_final_actions(
    final_actions: list[Any],
    editorial_map: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
    source_order: dict[str, int],
) -> list[PresentedEditorialItem]:
    actions = [
        dict(item) for item in final_actions
        if isinstance(item, dict) and str(item.get("source_id")) in source_by_id
    ]
    actions.sort(
        key=lambda item: (
            source_order.get(str(item.get("source_id")), 0),
            _integer(item.get("start_ms")),
            _integer(item.get("end_ms")),
        )
    )
    action_labels: dict[str, str] = {}
    result: list[PresentedEditorialItem] = []
    counters: dict[str, int] = {}
    for direction_number, item in enumerate(actions, 1):
        source = source_by_id[str(item["source_id"])]
        stem = Path(str(source.get("original_name") or "recording")).stem or "recording"
        counter_key = stem.casefold()
        counters[counter_key] = counters.get(counter_key, 0) + 1
        label = f"{stem}-{counters[counter_key]:03d}"
        action_id = str(item.get("action_id") or direction_number)
        item["id"] = action_id
        item["direction_number"] = direction_number
        action_labels[action_id] = label
        result.append(
            PresentedEditorialItem(
                key=f"recommendation:{action_id}",
                kind="recommendation",
                label=label,
                category=editorial_category("recommendation", item),
                source=source,
                item=item,
            )
        )
    support_counts: dict[str, int] = {}
    for item_value in editorial_map.get("supporting_edits", []):
        if not isinstance(item_value, dict):
            continue
        item = dict(item_value)
        source_id = str(item.get("source_id") or "")
        parent_id = str(item.get("parent_action_id") or "")
        if source_id not in source_by_id or parent_id not in action_labels:
            continue
        support_counts[parent_id] = support_counts.get(parent_id, 0) + 1
        suffix = chr(ord("a") + min(25, support_counts[parent_id] - 1))
        label = f"{action_labels[parent_id]}{suffix}"
        edit_id = str(item.get("edit_id") or label)
        item["id"] = edit_id
        result.append(
            PresentedEditorialItem(
                key=f"creative:{edit_id}",
                kind="creative",
                label=label,
                category=editorial_category("creative", item),
                source=source_by_id[source_id],
                item=item,
            )
        )
    return sorted(
        result,
        key=lambda value: (
            source_order.get(str(value.item.get("source_id")), 0),
            _integer(value.item.get("start_ms")),
            0 if value.kind == "recommendation" else 1,
        ),
    )


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
    action_type = str(item.get("action_type") or "")
    if action_type:
        return action_type
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
    if category not in {"cut", "condense", "voiceover", "keep", "connect_review", "creative"}:
        return action_label(category, locale).upper()
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
    if item.get("action_type"):
        return _first_text(
            item.get("instruction"),
            locale_label(locale, "Review this editorial direction.", "この編集方針を確認します。"),
        )
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
    return f"{action} {reason}".strip()


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
