"""Bounded, unauthenticated Wikipedia context for reusable game knowledge."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote, urlencode

from .errors import SubtitlerError
from .hosted_http import request_json


def lookup_game_wiki(title: str) -> dict[str, Any]:
    query = " ".join(title.split())
    if not query:
        return {"status": "unavailable", "detail": "empty title"}
    for language, suffix in (("en", " video game"), ("ja", " ゲーム")):
        try:
            result = _lookup(language, query, suffix)
        except Exception as exc:
            last_error = str(exc)[:300]
            continue
        if result is not None:
            return result
    return {"status": "unavailable", "detail": locals().get("last_error", "no confident match")}


def _lookup(language: str, title: str, suffix: str) -> dict[str, Any] | None:
    api = f"https://{language}.wikipedia.org/w/api.php"
    search_url = api + "?" + urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": title + suffix,
            "srlimit": 5,
            "format": "json",
            "utf8": 1,
        }
    )
    data = request_json(
        "GET",
        search_url,
        None,
        SubtitlerError,
        "Wikipedia game lookup failed",
        headers={"User-Agent": "SubUtl/1.0 (personal editorial assistant)"},
        timeout_sec=15,
        attempts=1,
    )
    results = data.get("query", {}).get("search", []) if isinstance(data.get("query"), dict) else []
    best: tuple[float, dict[str, Any]] | None = None
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        page_title = str(item.get("title") or "")
        score = SequenceMatcher(None, _key(title), _key(page_title)).ratio()
        snippet = re.sub(r"<[^>]+>", " ", str(item.get("snippet") or "")).casefold()
        game_signal = any(word in snippet for word in ("video game", "videogame", "ゲーム"))
        if score < 0.68 or not game_signal:
            continue
        if best is None or score > best[0]:
            best = (score, item)
    if best is None:
        return None
    page_title = str(best[1]["title"])
    extract_url = api + "?" + urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "titles": page_title,
            "format": "json",
            "utf8": 1,
        }
    )
    detail = request_json(
        "GET",
        extract_url,
        None,
        SubtitlerError,
        "Wikipedia game summary failed",
        headers={"User-Agent": "SubUtl/1.0 (personal editorial assistant)"},
        timeout_sec=15,
        attempts=1,
    )
    pages = detail.get("query", {}).get("pages", {}) if isinstance(detail.get("query"), dict) else {}
    page = next((value for value in pages.values() if isinstance(value, dict)), None) if isinstance(pages, dict) else None
    extract = " ".join(str(page.get("extract") or "").split())[:5000] if page else ""
    if not extract:
        return None
    return {
        "status": "complete",
        "language": language,
        "page_title": page_title,
        "url": f"https://{language}.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'))}",
        "match_confidence": round(best[0], 3),
        "summary": extract,
    }


def _key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())
