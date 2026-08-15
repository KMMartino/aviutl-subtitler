"""Canonical editorial operations shared by planning, reports, and EXO output.

The catalog is intentionally finite.  Natural-language instructions explain the
editorial judgment, while ``action_type`` identifies the concrete operation a
future automatic editor would dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ActionFamily = Literal["timeline", "narration", "accent", "continuity", "review"]
AutomationLevel = Literal["automatic", "assisted", "manual"]


@dataclass(frozen=True)
class EditorialActionSpec:
    action_type: str
    family: ActionFamily
    automation: AutomationLevel
    execution_method: str
    english_label: str
    japanese_label: str


# Do not add a synonym merely because a prompt used different wording.  Add an
# entry only when it requires a meaningfully different timeline operation.
EDITORIAL_ACTION_SPECS = (
    EditorialActionSpec("preserve", "timeline", "automatic", "leave_range_unchanged", "Keep", "維持"),
    EditorialActionSpec("trim", "timeline", "assisted", "remove_named_subranges", "Trim", "部分カット"),
    EditorialActionSpec("cut", "timeline", "automatic", "remove_range", "Cut", "カット"),
    EditorialActionSpec("extract_highlights", "timeline", "assisted", "retain_named_subranges", "Select highlights", "見どころ抽出"),
    EditorialActionSpec("montage", "timeline", "assisted", "assemble_representative_ranges", "Montage", "モンタージュ"),
    EditorialActionSpec("narrated_montage", "narration", "manual", "assemble_ranges_under_new_narration", "Narrated montage", "ナレーション付きモンタージュ"),
    EditorialActionSpec("narration_bridge", "narration", "manual", "replace_or_bridge_range_with_new_narration", "Narration bridge", "ナレーションで接続"),
    EditorialActionSpec("connect_ranges", "continuity", "assisted", "join_noncontiguous_ranges", "Connect moments", "場面を接続"),
    EditorialActionSpec("reorder_ranges", "continuity", "manual", "move_ranges_into_stated_order", "Reorder moments", "場面を並べ替え"),
    EditorialActionSpec("punch_in", "accent", "automatic", "animate_crop_scale", "Punch in", "寄り"),
    EditorialActionSpec("freeze_frame", "accent", "automatic", "hold_selected_frame", "Freeze frame", "フリーズフレーム"),
    EditorialActionSpec("replay", "accent", "automatic", "duplicate_range", "Replay", "リプレイ"),
    EditorialActionSpec("speed_change", "accent", "automatic", "retime_range", "Speed change", "速度変更"),
    EditorialActionSpec("emphasize_text", "accent", "automatic", "place_timed_text", "Emphasized text", "強調テキスト"),
    EditorialActionSpec("insert_reference_visual", "accent", "assisted", "place_verified_still_or_clip", "Reference visual", "参照映像"),
    EditorialActionSpec("visual_gag", "accent", "manual", "place_comedic_visual", "Visual gag", "映像ギャグ"),
    EditorialActionSpec("audio_accent", "accent", "manual", "place_or_adjust_audio_effect", "Audio accent", "音の演出"),
    EditorialActionSpec("foreshadow", "continuity", "manual", "add_earlier_setup_for_later_anchor", "Foreshadow", "伏線"),
    EditorialActionSpec("callback", "continuity", "manual", "reference_earlier_anchor_at_later_range", "Callback", "振り返り"),
    EditorialActionSpec("compare", "continuity", "assisted", "present_linked_ranges_for_comparison", "Compare", "比較"),
    EditorialActionSpec("intercut", "continuity", "manual", "alternate_linked_ranges", "Intercut", "交互編集"),
    EditorialActionSpec("manual_review", "review", "manual", "flag_without_timeline_mutation", "Manual review", "要確認"),
)

ACTION_SPEC_BY_TYPE = {spec.action_type: spec for spec in EDITORIAL_ACTION_SPECS}
CANONICAL_ACTION_TYPES = frozenset(ACTION_SPEC_BY_TYPE)
PRIMARY_ACTION_TYPES = frozenset(
    {
        "preserve",
        "trim",
        "cut",
        "extract_highlights",
        "montage",
        "narrated_montage",
        "narration_bridge",
        "connect_ranges",
        "reorder_ranges",
        "manual_review",
    }
)
SUPPORTING_ACTION_TYPES = CANONICAL_ACTION_TYPES - PRIMARY_ACTION_TYPES
REFERENCE_ACTION_TYPES = frozenset({"insert_reference_visual", "compare", "callback", "foreshadow"})

THREAD_RELATIONSHIP_TYPES = frozenset(
    {"setup_payoff", "foreshadow", "callback", "comparison", "recurrence", "causal_bridge", "topic_return"}
)

ACTION_COLORS = {
    "timeline": "#5ea1ff",
    "narration": "#bf83ff",
    "accent": "#ffad5c",
    "continuity": "#58d6b4",
    "review": "#a9b2bf",
}


def action_spec(action_type: object) -> EditorialActionSpec:
    return ACTION_SPEC_BY_TYPE.get(str(action_type), ACTION_SPEC_BY_TYPE["manual_review"])


def action_label(action_type: object, locale: str = "en") -> str:
    spec = action_spec(action_type)
    return spec.japanese_label if locale == "ja" else spec.english_label


def action_color(action_type: object) -> str:
    return ACTION_COLORS[action_spec(action_type).family]


def canonical_catalog_for_prompt() -> str:
    return "\n".join(
        f"- {spec.action_type}: {spec.execution_method} ({spec.family}; {spec.automation})"
        for spec in EDITORIAL_ACTION_SPECS
    )
