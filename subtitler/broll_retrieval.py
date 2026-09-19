"""Scene-level lexical retrieval over a complete, visibility-filtered snapshot."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from typing import Sequence

from .media_tags import CONTEXT_CATEGORIES
from .broll import (
    PER_NEED_KIND_LIMIT, BrollNeed, CatalogAsset, CatalogSegment,
    _asset_need_score, _search_tokens,
)


def retrieve_scenes(assets: Sequence[CatalogAsset], needs: Sequence[BrollNeed]) -> list[CatalogAsset]:
    rows: list[tuple[CatalogAsset, CatalogSegment | None, str]] = []
    for asset in assets:
        for segment in asset.segments:
            context_tags = " ".join(tag for tag in asset.tags if tag.partition(":")[0] in CONTEXT_CATEGORIES)
            text = f"{context_tags} {segment.observed_label} {segment.description} {' '.join(segment.tags)} {segment.suitability}"
            rows.append((asset, segment, text.casefold()))
        # File-level matches remain discovery candidates, but cannot lend their
        # descriptions to arbitrary intervals inside a segmented video.
        rows.append((asset, None, f"{asset.title} {asset.description} {' '.join(asset.tags)}".casefold()))
    selected: dict[str, CatalogAsset] = {}
    with closing(sqlite3.connect(":memory:")) as index:
        index.execute("CREATE VIRTUAL TABLE scenes USING fts5(content, tokenize='unicode61')")
        index.executemany("INSERT INTO scenes(rowid, content) VALUES (?, ?)",
                          ((i + 1, row[2]) for i, row in enumerate(rows)))
        for need in needs:
            terms = list(dict.fromkeys(_search_tokens(" ".join((need.description, *need.search_terms)))))
            if not terms:
                continue
            query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            hits = {int(row[0]) - 1: -float(row[1]) for row in index.execute(
                "SELECT rowid, bm25(scenes) FROM scenes WHERE scenes MATCH ?", (query,))}
            # unicode61 does not segment Japanese words. Explicit multilingual
            # phrases retain substring recall without requiring a hosted embedder.
            for i, (_, _, content) in enumerate(rows):
                if any(any(ord(char) > 127 for char in term) and term in content for term in terms):
                    hits.setdefault(i, 0.0)
            ranked = []
            for i, lexical in hits.items():
                asset, segment, content = rows[i]
                if need.preferred_media not in ("either", asset.media_kind):
                    continue
                if segment is None:
                    score = _asset_need_score(replace(asset, segments=()), need)
                    if asset.segments:
                        score *= .2
                else:
                    label = segment.observed_label.casefold()
                    score = sum(8 if term in label else 2 for term in terms if term in content)
                    score += sum(20 for phrase in need.search_terms if phrase.casefold() in label)
                    score += segment.confidence + (1 if segment.locked else 0)
                ranked.append((score + min(lexical, 1.0), i))
            counts = {"video": 0, "image": 0}
            for _, i in sorted(ranked, key=lambda item: (-item[0], item[1])):
                asset, segment, _ = rows[i]
                if counts[asset.media_kind] >= PER_NEED_KIND_LIMIT:
                    continue
                counts[asset.media_kind] += 1
                existing = selected.get(asset.id)
                segments = existing.segments if existing else ()
                if segment is not None and all(item.id != segment.id for item in segments):
                    segments = (*segments, segment)
                selected[asset.id] = replace(asset, segments=tuple(sorted(segments, key=lambda item: item.start_sec)))
    return list(selected.values())
