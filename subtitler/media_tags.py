"""Read the library's attributed tags without mutating its database."""
from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any

TAG_CATEGORIES = frozenset({"game", "platform", "subject", "action", "tone", "role", "category", "creator", "source", "format", "keyword"})
CONTEXT_CATEGORIES = frozenset({"game", "platform", "creator", "source", "format"})


def normalize_tag(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).lower()


def parse_tag(value: str) -> tuple[str, str]:
    category, separator, text = value.partition(":")
    category = normalize_tag(category)
    if not separator:
        category, text = "keyword", value
    if category not in TAG_CATEGORIES or not text.strip() or len(text.strip()) > 160:
        raise ValueError("Expected category:value using a supported tag category")
    return category, normalize_tag(text)


def read_tags(db: sqlite3.Connection, asset_id: str) -> list[dict[str, Any]]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_media_tags'").fetchone():
        return []
    return [dict(row) for row in db.execute(
        "SELECT id,segment_id,category,value,normalized_value,origin,evidence,confidence "
        "FROM effective_media_tags WHERE asset_id=? ORDER BY segment_id,category,normalized_value,origin", (asset_id,))]


def tag_labels(records: list[dict[str, Any]], segment_id: str = "") -> tuple[str, ...]:
    return tuple(dict.fromkeys(f"{row['category']}:{row['value']}" for row in records if row["segment_id"] == segment_id))


def scene_tags(records: list[dict[str, Any]], segment_id: str) -> list[dict[str, Any]]:
    """Inherit identity metadata, never claim a whole-file action occurs in every scene."""
    local = [row for row in records if row["segment_id"] == segment_id]
    categories = {row["category"] for row in local}
    inherited = [row for row in records if not row["segment_id"]
                 and row["category"] in CONTEXT_CATEGORIES and row["category"] not in categories]
    return [*local, *inherited]
