"""Grounded web discovery for missing B-roll assets.

Discovery never downloads or promotes a file. Returned pages remain candidates
until the user verifies rights/provenance and explicitly starts acquisition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .api_usage import ApiUsageLedger
from .errors import ModelLoadError
from .external_transcribers import require_api_key
from .hosted_http import request_json
from .model_prompts import WEB_DISCOVERY_SYSTEM_PROMPT


@dataclass(frozen=True)
class WebAssetCandidate:
    url: str
    title: str
    need_description: str
    rights_status: str = "unverified"


def discover_web_assets(
    needs: Sequence[Any],
    *,
    model: str,
    usage: ApiUsageLedger,
    api_key: str | None = None,
) -> list[WebAssetCandidate]:
    if not needs:
        return []
    key = api_key or require_api_key("OPENAI_API_KEY")
    need_lines = "\n".join(
        f"- Transcript lines {item.start_line}-{item.end_line}: {item.description}. Why needed: {item.reason}"
        for item in needs[:20]
    )
    data = request_json(
        "POST",
        "https://api.openai.com/v1/responses",
        {
            "model": model,
            "instructions": WEB_DISCOVERY_SYSTEM_PROMPT,
            "reasoning": {"effort": "low"},
            "tools": [{"type": "web_search", "search_context_size": "low"}],
            "tool_choice": "required",
            "include": ["web_search_call.action.sources"],
            "input": (
                "Task: find legitimate source pages for each supplied B-roll need. Search for video-game "
                "trailers, official gameplay, press assets, "
                "or other footage matching these B-roll needs. Prefer official developer/publisher channels "
                "and pages with explicit reuse terms. Record reuse rights as unverified pending human review. "
                "Completion means every need has been searched, each supported result cites its source page, "
                "and needs without a grounded result remain unanswered rather than receiving a guessed URL.\n\n"
                + need_lines
            ),
        },
        ModelLoadError,
        "OpenAI B-roll web discovery failed",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout_sec=180.0,
    )
    token_usage = data.get("usage") or {}
    usage.add(
        provider="openai",
        model=model,
        operation="broll_web_search",
        input_tokens=int(token_usage.get("input_tokens") or 0),
        output_tokens=int(token_usage.get("output_tokens") or 0),
        total_tokens=int(token_usage.get("total_tokens") or 0),
    )
    candidates: dict[str, WebAssetCandidate] = {}
    for output in data.get("output") or []:
        if not isinstance(output, dict):
            continue
        if output.get("type") == "message":
            for content in output.get("content") or []:
                if not isinstance(content, dict):
                    continue
                for annotation in content.get("annotations") or []:
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    _add_candidate(candidates, annotation.get("url"), annotation.get("title"), needs)
        if output.get("type") == "web_search_call":
            action = output.get("action") or {}
            if isinstance(action, dict):
                for source in action.get("sources") or []:
                    if isinstance(source, dict):
                        _add_candidate(candidates, source.get("url"), source.get("title"), needs)
    return list(candidates.values())[:50]


def _add_candidate(
    candidates: dict[str, WebAssetCandidate],
    raw_url: Any,
    raw_title: Any,
    needs: Sequence[Any],
) -> None:
    url = str(raw_url or "").strip()
    if not url.startswith(("https://", "http://")) or len(url) > 8192:
        return
    title = str(raw_title or url).strip()[:1000]
    need_description = "; ".join(str(item.description) for item in needs[:3])[:2000]
    candidates.setdefault(url, WebAssetCandidate(url, title, need_description))
