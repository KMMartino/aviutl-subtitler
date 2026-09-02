"""Hosted API subtitle cleanup/refinement backends."""

from __future__ import annotations

import base64
import json
import re
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .api_usage import ApiUsageLedger
from .errors import ModelLoadError, StructuredOutputIncompleteError
from .external_transcribers import require_api_key, verify_gemini_model_available, verify_openai_model_available
from .glossary import GlossaryEntry, format_glossary
from .hosted_http import request_json
from .model_prompts import model_system_prompt
from .models import ChapterSuggestion, MisTranscriptionFlag, SplitPlanResult
from .text_refiner import (
    TextRefiner,
    _clean_response_line,
    _dedupe_mistranscription_flags,
    _deterministic_mistranscription_flags,
    _parse_boundary_selection,
    _parse_mistranscription_flags,
    _valid_cleaned_line,
    boundary_selection_prompt,
    cleanup_base_rules,
    mistranscription_review_prompt,
)


YOUTUBE_CHAPTER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["start_line", "end_line", "title"],
                "additionalProperties": False,
            },
        },
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "after_line": {"type": "integer"},
                    "previous_topic": {"type": "string"},
                    "next_topic": {"type": "string"},
                },
                "required": ["after_line", "previous_topic", "next_topic"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["chapters", "cuts"],
    "additionalProperties": False,
}


class HostedTextRefiner(TextRefiner):
    provider = ""

    def __init__(
        self,
        model: str,
        glossary: list[GlossaryEntry],
        usage: ApiUsageLedger,
        structured_diagnostics_path: Path | None = None,
    ) -> None:
        self.model = model
        self.glossary = glossary
        self.usage = usage
        self.structured_diagnostics_path = structured_diagnostics_path
        self._structured_diagnostics_lock = threading.Lock()
        self.mode = "full"
        self.last_mistranscription_raw = ""
        self.last_youtube_chapters_raw = ""
        self.last_youtube_chapter_cuts: list[dict[str, Any]] = []

    def refine(self, lines: list[str]) -> list[str]:
        if not lines:
            return lines
        if len(lines) == 1:
            refined = self._refine_one(lines[0])
            return [refined if refined is not None else lines[0]]
        refined = self._refine_many(lines)
        if refined is not None:
            return refined
        return [self._refine_one(line) or line for line in lines]

    def supports_multi_split(self) -> bool:
        return True

    def split_input_capacity(self, max_chars: int) -> int:
        return max(2000, max_chars * 100)

    def select_split_boundaries(
        self,
        text: str,
        annotated_text: str,
        candidate_ids: list[str],
        max_chars: int,
        *,
        multiple: bool = False,
    ) -> SplitPlanResult:
        prompt = boundary_selection_prompt(
            annotated_text,
            candidate_ids,
            max_chars,
            multiple=multiple,
        )
        output_budget = min(256, max(32, len(candidate_ids) * 4))
        request_attempts: list[dict[str, Any]] = []

        def record_request_attempt(event: dict[str, Any]) -> None:
            request_attempts.append(
                {
                    **event,
                    "provider": self.provider,
                    "model": self.model,
                    "operation": "split",
                    "source_chars": len(text),
                    "annotated_chars": len(annotated_text),
                    "prompt_chars": len(prompt),
                    "candidate_count": len(candidate_ids),
                    "zone_count": _split_zone_count(candidate_ids),
                    "max_chars": max_chars,
                    "max_tokens": output_budget,
                }
            )

        try:
            raw = self._chat(
                prompt,
                max_tokens=output_budget,
                operation="split",
                attempt_observer=record_request_attempt,
            )
        except Exception as exc:
            print(f"Warning: hosted boundary selection failed; using deterministic split. {exc}")
            return SplitPlanResult(
                selected_ids=None,
                candidate_ids=candidate_ids,
                accepted=False,
                reject_reason="request_failed",
                input_text=text,
                request_attempts=request_attempts,
            )
        result = _parse_boundary_selection(
            raw,
            text=text,
            candidate_ids=candidate_ids,
            multiple=multiple,
        )
        result.request_attempts = request_attempts
        return result

    def flag_mistranscriptions(self, numbered_lines: list[tuple[int, str]]) -> list[MisTranscriptionFlag]:
        if not numbered_lines:
            return []
        flags: list[MisTranscriptionFlag] = []
        raw_blocks: list[str] = []
        batch_size = 16
        total_batches = (len(numbered_lines) + batch_size - 1) // batch_size
        print(f"Final candidate review: {len(numbered_lines)} subtitles in {total_batches} batch(es).", flush=True)
        for start in range(0, len(numbered_lines), batch_size):
            batch = numbered_lines[start : start + batch_size]
            batch_number = start // batch_size + 1
            print(
                f"Final candidate review batch {batch_number}/{total_batches}: "
                f"lines {batch[0][0]}-{batch[-1][0]}...",
                flush=True,
            )
            try:
                raw = self._chat(self._mistranscription_prompt(batch), max_tokens=1024, operation="mistranscription")
            except Exception as exc:
                print(f"Warning: final mistranscription check failed for lines {batch[0][0]}-{batch[-1][0]}; continuing. {exc}")
                continue
            raw_blocks.append(f"=== lines {batch[0][0]}-{batch[-1][0]} ===\n{raw.strip()}")
            batch_flags = _parse_mistranscription_flags(raw, batch)
            flags.extend(batch_flags)
            print(
                f"Final candidate review batch {batch_number}/{total_batches}: {len(batch_flags)} candidate(s).",
                flush=True,
            )
        deterministic_flags = _deterministic_mistranscription_flags(numbered_lines)
        flags.extend(deterministic_flags)
        self.last_mistranscription_raw = "\n\n".join(raw_blocks)
        return _dedupe_mistranscription_flags(flags)

    def should_move_leading_phrase_left(self, previous_text: str, current_text: str, phrase: str) -> bool:
        prompt = (
            "Task:\n"
            "Decide whether the leading Japanese connective/punctuation phrase in the current subtitle "
            "belongs at the end of the previous subtitle.\n\n"
            "Rules:\n"
            "- Output exactly MOVE or KEEP.\n"
            "- MOVE if the leading phrase clearly continues or completes the previous clause/sentence.\n"
            "- KEEP if the leading phrase is a valid discourse opener for the current sentence.\n"
            "- KEEP if either choice is plausible or context is insufficient.\n"
            "- Do not rewrite text.\n\n"
            f"Leading phrase: {phrase}\n"
            f"Previous subtitle: {previous_text}\n"
            f"Current subtitle: {current_text}\n\n"
            "Answer:"
        )
        try:
            raw = self._chat(prompt, max_tokens=8, operation="boundary")
        except Exception as exc:
            print(f"Warning: boundary phrase review failed; keeping subtitle boundary. {exc}")
            return False
        return raw.strip().upper().startswith("MOVE")

    def suggest_chapters(self, numbered_subtitles: list[tuple[int, float, float, str]]) -> list[ChapterSuggestion]:
        if not numbered_subtitles:
            return []
        prompt = self._youtube_chapters_prompt(numbered_subtitles)
        try:
            raw = self._chat(
                prompt,
                max_tokens=2048,
                operation="youtube_chapters",
                response_schema=YOUTUBE_CHAPTER_RESPONSE_SCHEMA,
            )
        except Exception as exc:
            print(f"Warning: YouTube chapter generation failed; continuing without chapter markers. {exc}", flush=True)
            self.last_youtube_chapters_raw = ""
            self.last_youtube_chapter_cuts = []
            return []
        self.last_youtube_chapters_raw = raw
        chapters, cuts = parse_youtube_chapter_response(raw, numbered_subtitles)
        self.last_youtube_chapter_cuts = cuts
        if not chapters:
            print("Warning: YouTube chapter generation returned no usable chapters.", flush=True)
        return chapters

    def complete_structured(
        self,
        prompt: str,
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Run a provider-neutral structured planning request through this hosted backend."""
        return self._chat(
            prompt,
            max_tokens=max_tokens,
            operation=operation,
            response_schema=response_schema,
        )

    def _base_rules(self) -> str:
        return cleanup_base_rules(self.mode)

    def _prompt_one(self, line: str) -> str:
        glossary = format_glossary(self.glossary)
        glossary_block = f"\nSpelling-reference glossary (entries are not correction candidates):\n{glossary}\n" if glossary else ""
        return (
            "Task:\nClean this subtitle text.\n\n"
            f"Rules:\n{self._base_rules()}\n"
            "- Output only the cleaned subtitle text.\n"
            f"{glossary_block}\nSubtitle:\n{line}"
        )

    def _prompt_many(self, lines: list[str]) -> str:
        glossary = format_glossary(self.glossary)
        glossary_block = f"\nSpelling-reference glossary (entries are not correction candidates):\n{glossary}\n" if glossary else ""
        numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
        return (
            "Task:\nClean these subtitle lines.\n\n"
            f"Rules:\n{self._base_rules()}\n"
            "- Keep the same number of lines.\n"
            "- Keep each line in the same order.\n"
            "- Output only the cleaned lines, one per line.\n"
            "- Do not add numbering, bullets, notes, or explanations.\n"
            f"{glossary_block}\nLines:\n{numbered}"
        )

    def _mistranscription_prompt(self, numbered_lines: list[tuple[int, str]]) -> str:
        return mistranscription_review_prompt(numbered_lines)

    def _youtube_chapters_prompt(self, numbered_subtitles: list[tuple[int, float, float, str]]) -> str:
        lines = "\n".join(
            f"{line_number}\t{start:.3f}\t{end:.3f}\t{text}"
            for line_number, start, end, text in numbered_subtitles
        )
        return (
            "Task:\n"
            "Analyze the full final subtitle transcript and identify coherent YouTube-style chapters.\n\n"
            "Rules:\n"
            "- Use the entire transcript so chapter titles share a consistent through line.\n"
            "- Return topic spans that cover the transcript in order.\n"
            "- Titles must be short phrases suitable for YouTube chapter names.\n"
            "- Prefer meaningful topic changes over frequent small cuts.\n"
            "- Do not translate unless the transcript itself changes language.\n"
            "- Complete coverage means every supplied line belongs to one ordered chapter span.\n"
            "- Return only the JSON object required by the response schema.\n\n"
            "Subtitle lines are tab-separated as line_number, start_seconds, end_seconds, text:\n"
            f"{lines}"
        )

    def _refine_one(self, line: str) -> str | None:
        try:
            raw = self._chat(self._prompt_one(line), operation="cleanup")
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned = _clean_response_line(raw)
        return cleaned if _valid_cleaned_line(cleaned) else None

    def _refine_many(self, lines: list[str]) -> list[str] | None:
        output_budget = min(16384, max(1024, sum(len(line) for line in lines) * 6))
        try:
            raw = self._chat(
                self._prompt_many(lines),
                max_tokens=output_budget,
                operation="cleanup",
            )
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned_lines = [_clean_response_line(line) for line in raw.splitlines() if line.strip()]
        if len(cleaned_lines) != len(lines):
            return None
        if any(not _valid_cleaned_line(line) for line in cleaned_lines):
            return None
        return cleaned_lines

    def _chat(
        self,
        prompt: str,
        max_tokens: int = 512,
        operation: str = "cleanup",
        attempt_observer: Callable[[dict[str, Any]], None] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError

    def _record_structured_response(
        self,
        *,
        operation: str,
        max_tokens: int,
        finish_reason: str,
        content: str,
        usage: dict[str, Any],
        schema_enabled: bool,
    ) -> None:
        if self.structured_diagnostics_path is None or not operation.startswith("editorial_"):
            return
        record = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider": self.provider,
            "model": self.model,
            "operation": operation,
            "max_output_tokens": max_tokens,
            "finish_reason": finish_reason,
            "schema_enabled": schema_enabled,
            "usage": usage,
            "response_content": content,
        }
        path = self.structured_diagnostics_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._structured_diagnostics_lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _hosted_text_timeout(prompt: str, max_tokens: int, operation: str = "cleanup") -> float:
    prompt_chars = len(prompt)
    estimated_seconds = prompt_chars / 60.0 + max_tokens / 25.0
    if operation == "split":
        return min(1200.0, max(90.0, estimated_seconds * 2.0))
    return min(600.0, max(45.0, estimated_seconds))


def _split_zone_count(candidate_ids: list[str]) -> int:
    zones = {
        match.group(0)
        for candidate_id in candidate_ids
        if (match := re.match(r"Z\d+", candidate_id.upper())) is not None
    }
    return len(zones)


def parse_youtube_chapter_response(
    raw: str,
    numbered_subtitles: list[tuple[int, float, float, str]],
) -> tuple[list[ChapterSuggestion], list[dict[str, Any]]]:
    if not raw.strip() or not numbered_subtitles:
        return [], []
    try:
        data = json.loads(_extract_json_object(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    raw_chapters = data.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        return [], []

    valid_indexes = [line_number for line_number, _, _, _ in numbered_subtitles]
    min_index = min(valid_indexes)
    max_index = max(valid_indexes)
    chapters: list[ChapterSuggestion] = []
    previous_end = min_index - 1
    for raw_chapter in raw_chapters:
        if not isinstance(raw_chapter, dict):
            return [], []
        try:
            start = int(raw_chapter.get("start_line"))
            end = int(raw_chapter.get("end_line"))
        except (TypeError, ValueError):
            return [], []
        start = max(min_index, min(max_index, start))
        end = max(min_index, min(max_index, end))
        if end < start or start <= previous_end:
            return [], []
        if start > previous_end + 1:
            start = previous_end + 1
        title = _chapter_title(raw_chapter.get("title"), len(chapters) + 1)
        chapters.append(ChapterSuggestion(start_subtitle_index=start, end_subtitle_index=end, title=title))
        previous_end = end

    if not chapters:
        return [], []
    if chapters[0].start_subtitle_index != min_index:
        chapters[0].start_subtitle_index = min_index
    if chapters[-1].end_subtitle_index < max_index:
        chapters[-1].end_subtitle_index = max_index

    cuts = _parse_chapter_cuts(data.get("cuts"))
    by_after_line = {cut["after_line"]: cut for cut in cuts if isinstance(cut.get("after_line"), int)}
    for chapter in chapters:
        cut = by_after_line.get(chapter.end_subtitle_index)
        if cut:
            chapter.previous_topic = str(cut.get("previous_topic") or "").strip()
            chapter.next_topic = str(cut.get("next_topic") or "").strip()
    return chapters, cuts


def _extract_json_object(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    return text[start : end + 1]


def _chapter_title(value: Any, index: int) -> str:
    title = str(value or "").strip()
    title = " ".join(title.split())
    if not title or len(title) > 60:
        return f"Chapter {index}"
    return title


def _parse_chapter_cuts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cuts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            after_line = int(item.get("after_line"))
        except (TypeError, ValueError):
            continue
        cuts.append(
            {
                "after_line": after_line,
                "previous_topic": str(item.get("previous_topic") or "").strip(),
                "next_topic": str(item.get("next_topic") or "").strip(),
            }
        )
    return cuts


class OpenAITextRefiner(HostedTextRefiner):
    provider = "openai"

    def __init__(
        self,
        model: str,
        glossary: list[GlossaryEntry],
        usage: ApiUsageLedger,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        structured_diagnostics_path: Path | None = None,
    ) -> None:
        super().__init__(model, glossary, usage, structured_diagnostics_path)
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
        self.reasoning_effort = reasoning_effort
        verify_openai_model_available(model, self.api_key)

    def _chat(
        self,
        prompt: str,
        max_tokens: int = 512,
        operation: str = "cleanup",
        attempt_observer: Callable[[dict[str, Any]], None] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        structured_operation = operation.startswith("editorial_")
        structured_request = response_schema is not None or structured_operation
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _openai_system_prompt(operation)},
                {"role": "user", "content": prompt},
            ],
        }
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _response_schema_name(operation),
                    "strict": True,
                    "schema": response_schema,
                },
            }
        elif structured_operation:
            payload["response_format"] = {"type": "json_object"}
        if not self.model.startswith("gpt-5"):
            payload["temperature"] = 0.0
        if operation == "split" and self.model.startswith("gpt-5"):
            payload["reasoning_effort"] = "none"
        elif self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        payload[_openai_max_tokens_key(self.model)] = max_tokens
        data = _request_json_with_retries(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout_sec=_hosted_text_timeout(prompt, max_tokens, operation),
            message=f"OpenAI hosted text {operation} request failed",
            attempt_observer=attempt_observer,
        )
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        self.usage.add(
            provider=self.provider,
            model=self.model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(usage.get("total_tokens") or input_tokens + output_tokens),
        )
        choice = data.get("choices", [{}])[0]
        message_data = choice.get("message", {})
        finish_reason = str(choice.get("finish_reason") or "unknown")
        refusal = str(message_data.get("refusal") or "")
        content = str(message_data.get("content") or refusal)
        self._record_structured_response(
            operation=operation,
            max_tokens=max_tokens,
            finish_reason=finish_reason,
            content=content,
            usage=usage,
            schema_enabled=response_schema is not None,
        )
        if structured_request and refusal:
            raise StructuredOutputIncompleteError(
                "OpenAI editorial response was refused",
                reason="refusal",
            )
        if structured_request and finish_reason != "stop":
            reason = "max_output_tokens" if finish_reason == "length" else finish_reason
            raise StructuredOutputIncompleteError(
                f"OpenAI editorial response ended before completion ({reason})",
                reason=reason,
            )
        if structured_request and not content.strip():
            raise StructuredOutputIncompleteError(
                "OpenAI structured response returned no output",
                reason="empty_output",
            )
        return content

    def complete_structured_with_images(
        self,
        prompt: str,
        images: list[tuple[Path, str]],
        *,
        max_tokens: int,
        operation: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Run one bounded structured Responses request with labeled frame evidence."""
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for path, label in images:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.extend(
                (
                    {"type": "input_text", "text": label},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "low",
                    },
                )
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": _openai_system_prompt(operation),
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": _response_schema_name(operation),
                    "strict": True,
                    "schema": response_schema,
                }
            },
        }
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        data = _request_json_with_retries(
            "POST",
            "https://api.openai.com/v1/responses",
            payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout_sec=_hosted_text_timeout(prompt, max_tokens, operation),
            message=f"OpenAI hosted visual {operation} request failed",
        )
        if data.get("status") == "incomplete":
            details = data.get("incomplete_details")
            reason = details.get("reason") if isinstance(details, dict) else "unknown"
            raise StructuredOutputIncompleteError(
                f"OpenAI editorial visual response ended before completion ({reason})",
                reason=str(reason),
            )
        parts: list[str] = []
        for output in data.get("output", []) if isinstance(data.get("output"), list) else []:
            if not isinstance(output, dict):
                continue
            for item in output.get("content", []) if isinstance(output.get("content"), list) else []:
                if isinstance(item, dict) and item.get("type") == "refusal":
                    raise StructuredOutputIncompleteError(
                        "OpenAI editorial visual response was refused",
                        reason="refusal",
                    )
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        response_text = "".join(parts)
        if not response_text.strip():
            raise StructuredOutputIncompleteError(
                "OpenAI editorial visual response returned no output",
                reason="empty_output",
            )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        self.usage.add(
            provider=self.provider,
            model=self.model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(usage.get("total_tokens") or input_tokens + output_tokens),
        )
        self._record_structured_response(
            operation=operation,
            max_tokens=max_tokens,
            finish_reason=str(data.get("status") or "completed"),
            content=response_text,
            usage=usage,
            schema_enabled=True,
        )
        return response_text


class GeminiTextRefiner(HostedTextRefiner):
    provider = "gemini"

    def __init__(
        self,
        model: str,
        glossary: list[GlossaryEntry],
        usage: ApiUsageLedger,
        api_key: str | None = None,
        thinking_level: str | None = None,
        structured_diagnostics_path: Path | None = None,
    ) -> None:
        super().__init__(model, glossary, usage, structured_diagnostics_path)
        self.api_key = api_key or require_api_key("GEMINI_API_KEY")
        self.thinking_level = thinking_level
        verify_gemini_model_available(model, self.api_key)

    def _chat(
        self,
        prompt: str,
        max_tokens: int = 512,
        operation: str = "cleanup",
        attempt_observer: Callable[[dict[str, Any]], None] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        generation_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = response_schema
        if self.model.startswith("gemini-3"):
            if operation == "split":
                generation_config["thinkingConfig"] = {
                    "thinkingLevel": "low" if self.model.startswith("gemini-3.1-pro") else "minimal"
                }
            elif self.thinking_level is not None:
                generation_config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        else:
            generation_config["temperature"] = 0.0
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {
                "parts": [{"text": _openai_system_prompt(operation)}]
            },
            "generationConfig": generation_config,
        }
        data = _request_json_with_retries(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(self.model)}:generateContent",
            payload,
            headers={"x-goog-api-key": self.api_key},
            timeout_sec=_hosted_text_timeout(prompt, max_tokens, operation),
            message=f"Gemini hosted text {operation} request failed",
            attempt_observer=attempt_observer,
        )
        usage = data.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        self.usage.add(
            provider=self.provider,
            model=self.model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(usage.get("totalTokenCount") or input_tokens + output_tokens),
        )
        candidate = data.get("candidates", [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        content = "".join(str(part.get("text", "")) for part in parts)
        finish_reason = str(candidate.get("finishReason") or "")
        self._record_structured_response(
            operation=operation,
            max_tokens=max_tokens,
            finish_reason=finish_reason,
            content=content,
            usage=usage,
            schema_enabled=response_schema is not None,
        )
        if response_schema is not None and finish_reason in {"MAX_TOKENS", "MALFORMED_FUNCTION_CALL"}:
            raise StructuredOutputIncompleteError(
                f"Gemini hosted text {operation} returned incomplete structured output",
                reason="max_output_tokens" if finish_reason == "MAX_TOKENS" else finish_reason.casefold(),
            )
        if response_schema is not None and not content.strip():
            raise StructuredOutputIncompleteError(
                f"Gemini hosted text {operation} returned no structured output",
                reason="empty_output",
            )
        return content


def _openai_system_prompt(operation: str) -> str:
    return model_system_prompt(operation)


def _response_schema_name(operation: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", operation).strip("_")
    return (cleaned or "structured_response")[:64]


def _request_json_with_retries(
    method: str,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_sec: float = 300.0,
    message: str = "Hosted text request failed",
    attempt_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return request_json(
        method,
        url,
        payload,
        ModelLoadError,
        message,
        headers=headers,
        timeout_sec=timeout_sec,
        attempt_observer=attempt_observer,
    )


def _openai_max_tokens_key(model: str) -> str:
    if model.startswith("gpt-5"):
        return "max_completion_tokens"
    return "max_tokens"
