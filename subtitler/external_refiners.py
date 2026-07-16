"""Hosted API subtitle cleanup/refinement backends."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .api_usage import ApiUsageLedger
from .errors import ModelLoadError
from .external_transcribers import require_api_key, verify_gemini_model_available, verify_openai_model_available
from .glossary import GlossaryEntry, format_glossary
from .hosted_http import request_json
from .models import ChapterSuggestion, MisTranscriptionFlag, SplitPlanResult
from .text_refiner import (
    TextRefiner,
    _clean_response_line,
    _dedupe_mistranscription_flags,
    _deterministic_mistranscription_flags,
    _parse_mistranscription_flags,
    _split_marker_response,
    _valid_cleaned_line,
    cleanup_base_rules,
    mistranscription_review_prompt,
    split_planning_prompt,
)


class HostedTextRefiner(TextRefiner):
    provider = ""

    def __init__(self, model: str, glossary: list[GlossaryEntry], usage: ApiUsageLedger) -> None:
        self.model = model
        self.glossary = glossary
        self.usage = usage
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

    def split_lines(self, text: str, max_chars: int) -> list[str] | None:
        return self.split_lines_with_diagnostics(text, max_chars).lines

    def split_lines_with_diagnostics(self, text: str, max_chars: int) -> SplitPlanResult:
        prompt = split_planning_prompt(text, max_chars)
        try:
            raw = self._chat(prompt, max_tokens=512, operation="split")
        except Exception as exc:
            print(f"Warning: hosted LLM split planning failed; using deterministic split. {exc}")
            return SplitPlanResult(lines=None, accepted=False, reject_reason="request_failed", input_text=text)
        raw_lines = [line for line in raw.splitlines() if line.strip()]
        lines = _split_marker_response(raw)
        if lines is None:
            cleaned_lines = [_clean_response_line(line) for line in raw_lines]
            if len(cleaned_lines) == 2:
                lines = cleaned_lines
        if lines is None:
            return SplitPlanResult(
                lines=None,
                raw_line_count=len(raw_lines),
                clean_line_count=0,
                accepted=False,
                reject_reason="missing_split_marker",
                input_text=text,
                raw_response=raw,
            )
        if any(not _valid_cleaned_line(line) for line in lines):
            return SplitPlanResult(
                lines=None,
                raw_line_count=len(raw_lines),
                clean_line_count=len([line for line in lines if line.strip()]),
                accepted=False,
                reject_reason="invalid_line",
                input_text=text,
                raw_response=raw,
                cleaned_lines=[line for line in lines if line.strip()],
            )
        lines = [line for line in lines if line.strip()]
        if len(lines) != 2:
            return SplitPlanResult(
                lines=None,
                raw_line_count=len(raw_lines),
                clean_line_count=len(lines),
                accepted=False,
                reject_reason="wrong_line_count",
                input_text=text,
                raw_response=raw,
                cleaned_lines=lines,
            )
        return SplitPlanResult(
            lines=lines,
            raw_line_count=len(raw_lines),
            clean_line_count=len(lines),
            accepted=True,
            reject_reason="none",
            input_text=text,
            raw_response=raw,
            cleaned_lines=lines,
        )

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
            raw = self._chat(prompt, max_tokens=2048, operation="youtube_chapters")
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
            "- Output strict JSON only. No markdown, comments, or explanations.\n\n"
            "Required JSON shape:\n"
            "{\n"
            "  \"chapters\": [\n"
            "    {\"start_line\": 1, \"end_line\": 12, \"title\": \"Intro\"}\n"
            "  ],\n"
            "  \"cuts\": [\n"
            "    {\"after_line\": 12, \"previous_topic\": \"Intro\", \"next_topic\": \"History\"}\n"
            "  ]\n"
            "}\n\n"
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
        try:
            raw = self._chat(self._prompt_many(lines), operation="cleanup")
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned_lines = [_clean_response_line(line) for line in raw.splitlines() if line.strip()]
        if len(cleaned_lines) != len(lines):
            return None
        if any(not _valid_cleaned_line(line) for line in cleaned_lines):
            return None
        return cleaned_lines

    def _chat(self, prompt: str, max_tokens: int = 512, operation: str = "cleanup") -> str:
        raise NotImplementedError


def _hosted_text_timeout(prompt: str, max_tokens: int) -> float:
    prompt_chars = len(prompt)
    estimated_seconds = prompt_chars / 60.0 + max_tokens / 25.0
    return min(600.0, max(45.0, estimated_seconds))


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
    ) -> None:
        super().__init__(model, glossary, usage)
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
        self.reasoning_effort = reasoning_effort
        verify_openai_model_available(model, self.api_key)

    def _chat(self, prompt: str, max_tokens: int = 512, operation: str = "cleanup") -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a meticulous subtitle QA reviewer. Follow the requested output format exactly."},
                {"role": "user", "content": prompt},
            ],
        }
        if not self.model.startswith("gpt-5"):
            payload["temperature"] = 0.0
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        payload[_openai_max_tokens_key(self.model)] = max_tokens
        data = _request_json_with_retries(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout_sec=_hosted_text_timeout(prompt, max_tokens),
            message=f"OpenAI hosted text {operation} request failed",
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
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))


class GeminiTextRefiner(HostedTextRefiner):
    provider = "gemini"

    def __init__(
        self,
        model: str,
        glossary: list[GlossaryEntry],
        usage: ApiUsageLedger,
        api_key: str | None = None,
        thinking_level: str | None = None,
    ) -> None:
        super().__init__(model, glossary, usage)
        self.api_key = api_key or require_api_key("GEMINI_API_KEY")
        self.thinking_level = thinking_level
        verify_gemini_model_available(model, self.api_key)

    def _chat(self, prompt: str, max_tokens: int = 512, operation: str = "cleanup") -> str:
        generation_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if self.model.startswith("gemini-3"):
            if self.thinking_level is not None:
                generation_config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        else:
            generation_config["temperature"] = 0.0
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {
                "parts": [{"text": "You are a meticulous subtitle QA reviewer. Follow the requested output format exactly."}]
            },
            "generationConfig": generation_config,
        }
        data = _request_json_with_retries(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(self.model)}:generateContent",
            payload,
            headers={"x-goog-api-key": self.api_key},
            timeout_sec=_hosted_text_timeout(prompt, max_tokens),
            message=f"Gemini hosted text {operation} request failed",
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
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(str(part.get("text", "")) for part in parts)


def _request_json_with_retries(
    method: str,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_sec: float = 300.0,
    message: str = "Hosted text request failed",
) -> dict[str, Any]:
    return request_json(
        method,
        url,
        payload,
        ModelLoadError,
        message,
        headers=headers,
        timeout_sec=timeout_sec,
    )


def _openai_max_tokens_key(model: str) -> str:
    if model.startswith("gpt-5"):
        return "max_completion_tokens"
    return "max_tokens"
