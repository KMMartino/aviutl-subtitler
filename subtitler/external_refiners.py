"""Hosted API subtitle cleanup/refinement backends."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .api_usage import ApiUsageLedger
from .errors import ModelLoadError
from .external_transcribers import require_api_key, verify_gemini_model_available, verify_openai_model_available
from .glossary import GlossaryEntry, format_glossary
from .models import MisTranscriptionFlag, SplitPlanResult
from .text_refiner import (
    TextRefiner,
    _clean_response_line,
    _dedupe_mistranscription_flags,
    _deterministic_mistranscription_flags,
    _parse_mistranscription_flags,
    _split_marker_response,
    _valid_cleaned_line,
)


class HostedTextRefiner(TextRefiner):
    provider = ""

    def __init__(self, model: str, glossary: list[GlossaryEntry], usage: ApiUsageLedger) -> None:
        self.model = model
        self.glossary = glossary
        self.usage = usage
        self.mode = "full"
        self.last_mistranscription_raw = ""

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
        min_chars = max(6, max_chars // 4)
        prompt = (
            "Task:\n"
            "Insert exactly one split marker into this transcript.\n\n"
            "Rules:\n"
            "- Copy the input text exactly.\n"
            "- Insert the marker <SPLIT> exactly once at the best subtitle break.\n"
            "- Do not rewrite, summarize, translate, add, remove, or reorder any text.\n"
            f"- Both sides should be at least {min_chars} characters when possible.\n"
            f"- Prefer both sides to be at most {max_chars} characters when possible.\n"
            "- Choose a split point near the center if there is no strong natural break.\n"
            "- Prefer sentence, phrase, or clause boundaries.\n"
            "- Output only the copied text with <SPLIT> inserted. No explanations.\n\n"
            f"Input:\n{text}\n\n"
            "Output:"
        )
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

    def _base_rules(self) -> str:
        glossary_rule = "Keep technical terms exactly as written in the glossary."
        rules = [
            "Keep the same language.",
            "Do not translate.",
            "Do not summarize.",
            "Remove standalone filler sounds such as えー, あー, うーん, あの, その, and まあ when they do not add meaning, and fix glossary terms when clearly intended.",
            glossary_rule,
            "If the beginning or end looks like a broken partial word caused by an audio cut, remove it only if the remaining text is still grammatical.",
        ]
        return "\n".join(f"- {rule}" for rule in rules)

    def _prompt_one(self, line: str) -> str:
        glossary = format_glossary(self.glossary)
        glossary_block = f"\nGlossary:\n{glossary}\n" if glossary else ""
        return (
            "Task:\nClean this subtitle text.\n\n"
            f"Rules:\n{self._base_rules()}\n"
            "- Output only the cleaned subtitle text.\n"
            f"{glossary_block}\nSubtitle:\n{line}"
        )

    def _prompt_many(self, lines: list[str]) -> str:
        glossary = format_glossary(self.glossary)
        glossary_block = f"\nGlossary:\n{glossary}\n" if glossary else ""
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
        lines = "\n".join(f"{line_number}. {text}" for line_number, text in numbered_lines)
        return (
            "Task:\n"
            "Find only candidate subtitle text spans that are likely worth a human editor's attention. "
            "Favor precision over recall; this pass should not flag normal speech quirks.\n\n"
            "Rules:\n"
            "- Do not rewrite or correct the transcript.\n"
            "- Preserve intentional speaking quirks, casual particles, fillers, repeated emphasis, dialect, and streamer cadence unless they are clearly an ASR or cleanup artifact.\n"
            "- Flag likely ASR errors, clearly wrong proper nouns, broken English/product names, impossible dates or numbers, repeated fragments caused by model looping, partial words from subtitle cuts, cleanup artifacts, mojibake, glossary leaks, and topic-incoherent wording.\n"
            "- Do not flag merely awkward but plausible Japanese.\n"
            "- Flag short exact substrings when possible. If the suspicious part cannot be isolated, copy the whole subtitle line exactly.\n"
            "- Output one flagged item per line as: line_number<TAB>exact copied text segment<TAB>short reason\n"
            "- If you are unsure, do not flag it.\n"
            "- Output NONE only if there is truly nothing worth human review in this batch.\n"
            "- Do not output explanations, bullets, JSON, or extra text.\n\n"
            f"Transcript lines:\n{lines}"
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


class OpenAITextRefiner(HostedTextRefiner):
    provider = "openai"

    def __init__(self, model: str, glossary: list[GlossaryEntry], usage: ApiUsageLedger, api_key: str | None = None) -> None:
        super().__init__(model, glossary, usage)
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
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
        payload[_openai_max_tokens_key(self.model)] = max_tokens
        data = _request_json_with_retries(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
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

    def __init__(self, model: str, glossary: list[GlossaryEntry], usage: ApiUsageLedger, api_key: str | None = None) -> None:
        super().__init__(model, glossary, usage)
        self.api_key = api_key or require_api_key("GEMINI_API_KEY")
        verify_gemini_model_available(model, self.api_key)

    def _chat(self, prompt: str, max_tokens: int = 512, operation: str = "cleanup") -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {
                "parts": [{"text": "You are a meticulous subtitle QA reviewer. Follow the requested output format exactly."}]
            },
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": max_tokens},
        }
        data = _request_json_with_retries(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(self.model)}:generateContent"
            f"?key={urllib.parse.quote(self.api_key)}",
            payload,
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
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    for attempt in range(3):
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise ModelLoadError(f"Hosted text request failed: HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            if attempt == 2:
                raise ModelLoadError(f"Hosted text request failed: {exc}") from exc
        time.sleep(2**attempt)
    raise ModelLoadError("Hosted text request failed")


def _openai_max_tokens_key(model: str) -> str:
    if model.startswith("gpt-5"):
        return "max_completion_tokens"
    return "max_tokens"
