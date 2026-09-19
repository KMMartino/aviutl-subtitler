"""Read-only JSON search for agents: python -m subtitler.media_search --help."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence

from .broll import _search_tokens, load_catalog
from .media_tags import normalize_tag, parse_tag, read_tags, scene_tags


def search_media(
    database: Path, *, query: str = "", tags: Sequence[str] = (), media_kind: str | None = None,
    min_duration: float = 0, max_duration: float | None = None, min_confidence: float = 0,
    scope: str = "scenes", limit: int = 20, offset: int = 0,
) -> dict[str, Any]:
    if not database.is_file():
        raise ValueError("Media library database does not exist")
    if (scope not in {"scenes", "files"} or media_kind not in {None, "video", "image"}
            or not 1 <= limit <= 200 or offset < 0 or not math.isfinite(min_duration) or min_duration < 0
            or not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1
            or max_duration is not None and (not math.isfinite(max_duration) or max_duration < min_duration)):
        raise ValueError("Invalid media search filters")
    filters = [parse_tag(value) for value in tags]
    tokens = _search_tokens(query)
    results = []
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        for asset in load_catalog(database):
            if media_kind and asset.media_kind != media_kind:
                continue
            records = read_tags(db, asset.id)
            candidates = list(asset.segments) if scope == "scenes" and asset.segments else [None]
            for scene in candidates:
                selected_tags = scene_tags(records, scene.id) if scene else records
                matched_tags = []
                for category, value in filters:
                    matches = [tag for tag in selected_tags if tag["category"] == category and normalize_tag(str(tag["value"])) == value
                               and float(tag["confidence"]) >= min_confidence]
                    if not matches:
                        break
                    matched_tags.extend(matches)
                else:
                    start = scene.start_sec if scene else 0.0
                    end = scene.end_sec if scene else asset.duration_sec
                    duration = end - start if end is not None else None
                    if min_duration > 0 and (duration is None or duration < min_duration):
                        continue
                    if max_duration is not None and duration is not None and duration > max_duration:
                        continue
                    confidence = scene.confidence if scene else {"user": 1.0, "ai": .7, "inferred": 0.0}[asset.description_source]
                    if confidence < min_confidence:
                        continue
                    label = (scene.observed_label or scene.description) if scene else asset.title
                    description = scene.description if scene else asset.description
                    text = " ".join([label, description, asset.title, *(str(tag["value"]) for tag in selected_tags)]).casefold()
                    matched = [token for token in tokens if token in text]
                    if tokens and not matched:
                        continue
                    score = len(matched_tags) * 10 + sum(5 if token in label.casefold() else 1 for token in matched)
                    results.append({
                        "id": f"{asset.id}:{scene.id if scene else 'file'}", "asset_id": asset.id,
                        "scene_id": scene.id if scene else None, "path": str(asset.path), "title": asset.title,
                        "media_kind": asset.media_kind, "label": label, "description": description,
                        "start_sec": start, "end_sec": end, "duration_sec": duration, "confidence": confidence,
                        "tags": selected_tags, "matched_terms": matched,
                        "matched_tags": list(dict.fromkeys(f"{tag['category']}:{tag['value']}" for tag in matched_tags)),
                        "analysis_run_id": scene.analysis_run_id if scene else "",
                        "evidence_spacing_sec": scene.evidence_spacing_sec if scene else None,
                        "unresolved_question": scene.handoff_reason if scene else "",
                        "preview": {"path": str(asset.path), "start_sec": start, "end_sec": end},
                        "score": score,
                    })
    results.sort(key=lambda item: (-item["score"], -item["confidence"], item["id"]))
    return {"schema_version": 1, "total": len(results), "offset": offset, "results": results[offset:offset + limit]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--tag", action="append", default=[], help="Repeatable AND filter, e.g. game:Elden Ring")
    parser.add_argument("--kind", choices=["video", "image"])
    parser.add_argument("--scope", choices=["scenes", "files"], default="scenes")
    parser.add_argument("--min-duration", type=float, default=0)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--min-confidence", type=float, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    try:
        result = search_media(args.database, query=args.query, tags=args.tag, media_kind=args.kind, scope=args.scope,
            min_duration=args.min_duration, max_duration=args.max_duration, min_confidence=args.min_confidence,
            limit=args.limit, offset=args.offset)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
