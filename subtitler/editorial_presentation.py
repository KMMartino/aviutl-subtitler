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
    for kind, field in (
        ("recommendation", "recommendations"),
        ("narration", "narration_briefs"),
        ("creative", "creative_suggestions"),
    ):
        values = editorial_map.get(field, []) if isinstance(editorial_map, dict) else []
        for index, item in enumerate(values if isinstance(values, list) else []):
            if isinstance(item, dict) and str(item.get("source_id")) in source_by_id:
                raw_items.append((kind, index, item))
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
    return f"{action} {reason}".strip()


def backup_suggestion(presented: PresentedEditorialItem, locale: str = "en") -> str:
    item = presented.item
    if presented.kind == "creative":
        return _first_text(
            item.get("backup_option"),
            locale_label(locale, "Leave the moment unembellished if the accent distracts from the action.", "演出が本編を邪魔するなら、装飾せずそのまま残します。"),
        )
    if presented.kind == "narration":
        return _first_text(item.get("memory_jog"), locale_label(locale, "Keep a short live excerpt if narration is unnecessary.", "ナレーションが不要なら短い生音声の抜粋を残します。"))
    if presented.category in {"cut", "condense", "voiceover"}:
        return _first_text(
            item.get("continuity_case"),
            item.get("selection_case"),
            locale_label(locale, "Keep one short representative excerpt if the transition needs more context.", "つなぎに文脈が必要なら短い代表場面を一つ残します。"),
        )
    if presented.category == "keep":
        return _first_text(
            item.get("subtraction_case"),
            item.get("selection_case"),
            locale_label(locale, "Shorten the least informative portion while preserving the payoff.", "見せ場を守りながら情報量の少ない部分を短縮します。"),
        )
    return _first_text(
        item.get("selection_case"),
        item.get("continuity_case"),
        item.get("subtraction_case"),
        locale_label(locale, "Leave the section in place if the connection is unclear.", "つながりが不明なら区間をそのまま残します。"),
    )


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
