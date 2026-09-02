"""Deterministically align retained speech between finished videos and source VODs."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ALIGNMENT_VERSION = 1
_WORD = re.compile(r"[a-z0-9']+")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.lower()))


def _ngrams(tokens: tuple[str, ...], size: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return set()
    return {tokens[index:index + size] for index in range(len(tokens) - size + 1)}


def _load_transcript(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(row) for row in payload.get("transcript", []) if isinstance(row, dict)]


def _duration_ms(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    probe = payload.get("probe") if isinstance(payload, dict) else None
    return int(probe.get("duration_ms") or 0) if isinstance(probe, dict) else 0


def align_transcripts(
    finished: list[dict[str, Any]],
    vods: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    prepared: dict[str, list[tuple[str, ...]]] = {
        key: [_tokens(str(row.get("text") or "")) for row in rows]
        for key, rows in vods.items()
    }
    postings: dict[tuple[str, ...], list[tuple[str, int]]] = defaultdict(list)
    for key, rows in prepared.items():
        for index, tokens in enumerate(rows):
            for gram in _ngrams(tokens):
                postings[gram].append((key, index))

    matches: list[dict[str, Any]] = []
    for finished_index, row in enumerate(finished):
        tokens = _tokens(str(row.get("text") or ""))
        grams = _ngrams(tokens)
        if not grams:
            continue
        votes: Counter[tuple[str, int]] = Counter()
        for gram in grams:
            locations = postings.get(gram, [])
            if len(locations) > 20:
                continue
            weight = max(1, round(10 / math.sqrt(max(1, len(locations)))))
            for location in locations:
                votes[location] += weight
        best: tuple[float, str, int] | None = None
        for (vod_key, vod_index), vote in votes.most_common(16):
            candidate = prepared[vod_key][vod_index]
            overlap = len(grams & _ngrams(candidate)) / max(1, len(grams))
            sequence = SequenceMatcher(None, tokens, candidate, autojunk=False).ratio()
            score = 0.45 * overlap + 0.45 * sequence + 0.10 * min(1.0, vote / 10)
            if best is None or score > best[0]:
                best = (score, vod_key, vod_index)
        if best is None or best[0] < 0.52:
            continue
        _, vod_key, vod_index = best
        vod_row = vods[vod_key][vod_index]
        matches.append({
            "finished_index": finished_index,
            "finished_start_ms": int(row.get("start_ms") or 0),
            "finished_end_ms": int(row.get("end_ms") or 0),
            "finished_text": str(row.get("text") or ""),
            "vod_key": vod_key,
            "vod_index": vod_index,
            "vod_start_ms": int(vod_row.get("start_ms") or 0),
            "vod_end_ms": int(vod_row.get("end_ms") or 0),
            "vod_text": str(vod_row.get("text") or ""),
            "score": round(best[0], 4),
        })

    spans: list[dict[str, Any]] = []
    for match in matches:
        previous = spans[-1] if spans else None
        if (
            previous is not None
            and previous["vod_key"] == match["vod_key"]
            and match["finished_index"] <= previous["finished_end_index"] + 3
            and previous["vod_end_index"] <= match["vod_index"] <= previous["vod_end_index"] + 8
            and match["finished_start_ms"] <= previous["finished_end_ms"] + 12_000
        ):
            previous["finished_end_index"] = match["finished_index"]
            previous["finished_end_ms"] = match["finished_end_ms"]
            previous["vod_end_index"] = match["vod_index"]
            previous["vod_end_ms"] = match["vod_end_ms"]
            previous["match_count"] += 1
            continue
        spans.append({
            "finished_start_index": match["finished_index"],
            "finished_end_index": match["finished_index"],
            "finished_start_ms": match["finished_start_ms"],
            "finished_end_ms": match["finished_end_ms"],
            "vod_key": match["vod_key"],
            "vod_start_index": match["vod_index"],
            "vod_end_index": match["vod_index"],
            "vod_start_ms": match["vod_start_ms"],
            "vod_end_ms": match["vod_end_ms"],
            "match_count": 1,
        })

    finished_speech_ms = sum(
        max(0, int(row.get("end_ms") or 0) - int(row.get("start_ms") or 0))
        for row in finished
    )
    matched_speech_ms = sum(
        max(0, item["finished_end_ms"] - item["finished_start_ms"])
        for item in matches
    )
    return {
        "alignment_version": ALIGNMENT_VERSION,
        "finished_utterances": len(finished),
        "matched_utterances": len(matches),
        "finished_speech_ms": finished_speech_ms,
        "matched_speech_ms": matched_speech_ms,
        "matched_speech_ratio": round(matched_speech_ms / max(1, finished_speech_ms), 4),
        "matches": matches,
        "spans": spans,
    }


def run(manifest: Path, artifacts_root: Path, output_root: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    items = payload.get("items", []) if isinstance(payload, dict) else []
    by_key = {str(item.get("key")): item for item in items if isinstance(item, dict)}
    results = []
    for key, item in by_key.items():
        vod_keys = item.get("vod_keys") or ([item["vod_key"]] if item.get("vod_key") else [])
        if not vod_keys:
            continue
        finished_path = artifacts_root / key / "preprocessing.json"
        vod_paths = {str(vod_key): artifacts_root / str(vod_key) / "preprocessing.json" for vod_key in vod_keys}
        if not finished_path.is_file() or not all(path.is_file() for path in vod_paths.values()):
            continue
        result = align_transcripts(
            _load_transcript(finished_path),
            {vod_key: _load_transcript(path) for vod_key, path in vod_paths.items()},
        )
        finished_duration_ms = _duration_ms(finished_path)
        vod_duration_ms = sum(_duration_ms(path) for path in vod_paths.values())
        result.update({
            "status": "complete",
            "finished_key": key,
            "vod_keys": list(vod_paths),
            "finished_duration_ms": finished_duration_ms,
            "vod_duration_ms": vod_duration_ms,
            "source_to_finished_duration_ratio": round(
                vod_duration_ms / max(1, finished_duration_ms), 4
            ),
        })
        output = output_root / key / "finished-vod-alignment.json"
        _atomic_json(output, result)
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.artifacts, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
