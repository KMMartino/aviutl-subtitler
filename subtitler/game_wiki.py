"""Bounded, unauthenticated Wikipedia context for reusable game knowledge."""

from __future__ import annotations

import re
import unicodedata
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
    selected: dict[str, Any] | None = None
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        page_title = str(item.get("title") or "")
        snippet = re.sub(r"<[^>]+>", " ", str(item.get("snippet") or "")).casefold()
        game_signal = any(word in snippet for word in ("video game", "videogame", "ゲーム"))
        if not game_title_matches(title, page_title) or not game_signal:
            continue
        selected = item
        break
    if selected is None:
        return None
    page_title = str(selected["title"])
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
    # Search titles may redirect. The resolved target must establish the same
    # identity; lexical resemblance or an unverified alias is not sufficient.
    if not page or not game_title_matches(title, str(page.get("title") or "")):
        return None
    page_title = str(page["title"])
    extract = " ".join(str(page.get("extract") or "").split())[:5000] if page else ""
    if not extract:
        return None
    return {
        "status": "complete",
        "language": language,
        "page_title": page_title,
        "url": f"https://{language}.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'))}",
        "match_confidence": 1.0,
        "match_basis": "normalized_title",
        "summary": extract,
    }


def game_title_matches(title: str, page_title: str) -> bool:
    """Fail closed for mode labels, sequels, and aliases we cannot establish."""
    def key(value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        return re.sub(r"\s*\((?:\d{4} )?(?:video game|computer game|ゲーム|コンピュータゲーム)\)$", "", normalized)

    return bool(key(title)) and key(title) == key(page_title)
