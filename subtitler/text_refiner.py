"""Plain-text subtitle cleanup through a local llama.cpp server."""

from __future__ import annotations

import json
import re
import threading
import unicodedata
import urllib.request
from pathlib import Path

from .errors import ModelLoadError
from .glossary import GlossaryEntry
from .llama_server import LlamaServerProcess
from .models import ChapterSuggestion, MisTranscriptionFlag, SplitPlanResult


def cleanup_base_rules(mode: str, *, include_glossary: bool = True) -> str:
    mode_line = {
        "fillers": "Remove filler sounds such as えー, あー, うーん, あの, その, and まあ when doing so keeps the spoken flow natural.",
        "glossary": "Fix glossary terms when clearly intended." if include_glossary else "Do not rewrite names or terms.",
        "full": (
            "Remove filler sounds such as えー, あー, うーん, あの, その, and まあ when doing so keeps the spoken flow natural, "
            + ("and fix glossary terms when clearly intended." if include_glossary else "but do not rewrite names or terms.")
        ),
    }.get(mode, "Clean the subtitle text conservatively.")
    rules = [
        "Faithfully preserve what was likely spoken.",
        "Do not change semantic or factual content, including names, titles, products, events, dates, numbers, or negation.",
        "Do not replace one plausible proper noun or title with a different one based on subject knowledge or surrounding context.",
        "Keep the same language.",
        "Do not translate.",
        "Do not summarize.",
        "Do not make casual speech more formal or polished.",
        "Preserve plausible repetitions, self-corrections, and streamer-style phrasing.",
        mode_line,
        "If the beginning or end looks like a broken partial word caused by an audio cut, remove it only if the remaining text is still grammatical.",
    ]
    if include_glossary:
        rules.insert(
            -1,
            "Treat the glossary only as a spelling reference, not as a list of terms expected in the transcript.",
        )
        rules.insert(
            -1,
            "Use a glossary spelling only when the input is a close phonetic or orthographic match for that same term; "
            "if the input is already a plausible different term, preserve it.",
        )
    return "\n".join(f"- {rule}" for rule in rules)


def split_planning_prompt(text: str, max_chars: int) -> str:
    min_chars = max(6, max_chars // 4)
    return (
        "Task:\n"
        "Insert exactly one split marker into this transcript.\n\n"
        "Rules:\n"
        "- Copy the input text exactly.\n"
        "- Insert the marker <SPLIT> exactly once at the best subtitle break.\n"
        "- Do not rewrite, summarize, translate, add, remove, normalize, re-spell, or reorder any text.\n"
        "- Never split inside a contiguous English, product, event, game, or title name when it can be avoided.\n"
        "- Treat katakana runs, ASCII letters, digits, and title separators such as ・, -, /, and & as fragile title text.\n"
        "- If the text contains a list of game or event titles, prefer boundaries between list items.\n"
        f"- Both sides should be at least {min_chars} characters when possible.\n"
        f"- Prefer both sides to be at most {max_chars} characters when possible.\n"
        "- Choose a split point near the center if there is no strong natural break.\n"
        "- Prefer sentence, phrase, clause, or list-item boundaries.\n"
        "- Output only the copied text with <SPLIT> inserted. No explanations.\n\n"
        f"Input:\n{text}\n\n"
        "Output:"
    )


def mistranscription_review_prompt(numbered_lines: list[tuple[int, str]]) -> str:
    lines = "\n".join(f"{line_number}. {text}" for line_number, text in numbered_lines)
    return (
        "Task:\n"
        "Find only subtitle text spans likely worth a human editor's attention. "
        "Favor precision over recall; this pass should not flag normal speech quirks.\n\n"
        "Rules:\n"
        "- Do not rewrite or correct the transcript.\n"
        "- Preserve intentional speaking quirks, casual particles, fillers, repeated emphasis, dialect, and streamer cadence unless they are clearly an ASR or cleanup artifact.\n"
        "- Strongly flag broken product, game, event, or title names; suspicious English/Japanese title concatenation; partial cut fragments; bad subtitle-boundary joins; impossible dates or numbers; repeated model-loop fragments; cleanup artifacts; glossary leaks; mojibake; and topic-incoherent wording.\n"
        "- Do not flag merely awkward but plausible Japanese.\n"
        "- Flag short exact substrings when possible. If the suspicious part cannot be isolated, copy the whole subtitle line exactly.\n"
        "- Severity must be high, medium, or low.\n"
        "- Use high for clear correction candidates, medium for plausible issues worth checking, and low for weak/uncertain candidates.\n"
        "- Output one flagged item per line as: line_number<TAB>severity<TAB>exact copied text segment<TAB>short reason\n"
        "- If you are unsure, do not flag it.\n"
        "- Output NONE only if there is truly nothing worth human review in this batch.\n"
        "- Do not output explanations, bullets, JSON, or extra text.\n\n"
        "Examples:\n"
        "12\thigh\tゴッドオブウォートリロジーリメイク\tbroken product/title name\n"
        "38\tmedium\tではでは、こ\tpossible subtitle cut fragment\n\n"
        f"Transcript lines:\n{lines}"
    )


class TextRefiner:
    last_mistranscription_raw: str = ""

    def refine(self, lines: list[str]) -> list[str]:
        return lines

    def split_lines(self, text: str, max_chars: int) -> list[str] | None:
        return None

    def split_lines_with_diagnostics(self, text: str, max_chars: int) -> SplitPlanResult:
        lines = self.split_lines(text, max_chars)
        return SplitPlanResult(
            lines=lines,
            raw_line_count=len(lines or []),
            clean_line_count=len(lines or []),
            accepted=lines is not None,
            reject_reason="none" if lines is not None else "request_failed",
            input_text=text,
            cleaned_lines=lines or [],
        )

    def flag_mistranscriptions(self, numbered_lines: list[tuple[int, str]]) -> list[MisTranscriptionFlag]:
        return []

    def suggest_chapters(self, numbered_subtitles: list[tuple[int, float, float, str]]) -> list[ChapterSuggestion]:
        return []

    def should_move_leading_phrase_left(self, previous_text: str, current_text: str, phrase: str) -> bool:
        return False

    def close(self) -> None:
        return None


class LlamaServerTextRefiner(TextRefiner):
    def __init__(
        self,
        model_path: Path,
        server_path: Path | None,
        glossary: list[GlossaryEntry],
        mode: str,
        host: str = "127.0.0.1",
        port: int = 8082,
        ctx_size: int = 4096,
        n_gpu_layers: int = -1,
        spec_draft_model: Path | None = None,
        spec_draft_n_max: int = 3,
        mistranscription_batch_size: int = 16,
        log_path: Path | None = None,
        cleanup_diagnostics_path: Path | None = None,
    ) -> None:
        if not model_path.exists():
            raise ModelLoadError(f"Cleanup model not found: {model_path}")
        if spec_draft_model is not None and not spec_draft_model.exists():
            raise ModelLoadError(f"Speculative draft/MTP model not found: {spec_draft_model}")
        if spec_draft_model is not None and spec_draft_model.suffix.lower() != ".gguf":
            raise ModelLoadError(
                "Cleanup speculative draft/MTP model must be a GGUF file for llama.cpp. "
                f"Got: {spec_draft_model}"
            )
        self.model_path = model_path
        self.spec_draft_model = spec_draft_model
        self.spec_draft_n_max = max(1, spec_draft_n_max)
        self.mistranscription_batch_size = max(1, mistranscription_batch_size)
        self.glossary = glossary
        self.mode = mode
        self.host = host
        self.port = port
        self.ctx_size = ctx_size
        self.n_gpu_layers = n_gpu_layers
        self.base_url = f"http://{host}:{port}"
        self.last_mistranscription_raw = ""
        self.cleanup_diagnostics_path = cleanup_diagnostics_path
        if self.cleanup_diagnostics_path is not None:
            self.cleanup_diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            self.cleanup_diagnostics_path.unlink(missing_ok=True)
        self._cleanup_diagnostics_lock = threading.Lock()
        self._cleanup_diagnostics_sequence = 0
        self._chat_context = threading.local()
        # Newer llama.cpp builds distinguish disabling reasoning mode from giving
        # reasoning a zero-token budget.  The budget alone can leave the model in
        # reasoning mode and expose an untagged checklist in message.content.
        extra_args = ["--reasoning", "off", "--reasoning-budget", "0"]
        if self.spec_draft_model is not None:
            extra_args.extend(
                [
                    "--spec-draft-model",
                    str(self.spec_draft_model),
                    "--spec-type",
                    "draft-mtp",
                    "--spec-draft-n-max",
                    str(self.spec_draft_n_max),
                ]
            )
        print(f"Starting cleanup llama-server on {self.host}:{self.port}...", flush=True)
        print(f"Cleanup model: {self.model_path}", flush=True)
        if self.spec_draft_model is not None:
            print(f"Cleanup MTP draft model: {self.spec_draft_model}", flush=True)
        self._server = LlamaServerProcess(
            model_path=model_path,
            server_path=server_path,
            host=host,
            port=port,
            ctx_size=ctx_size,
            n_gpu_layers=n_gpu_layers,
            extra_args=extra_args,
            log_path=log_path,
            label="cleanup llama-server",
            ready_message="Cleanup model ready.",
            wait_message="Waiting for cleanup model to load...",
        )
        self.process = self._server.process

    def refine(self, lines: list[str]) -> list[str]:
        if self.mode == "off" or not lines:
            return lines
        if len(lines) == 1:
            refined_line = self._refine_one(lines[0])
            return [refined_line if refined_line is not None else lines[0]]
        refined_lines = self._refine_many(lines)
        if refined_lines is not None:
            return refined_lines
        print(
            f"Warning: cleanup response rejected; retaining {len(lines)} original subtitle(s).",
            flush=True,
        )
        return lines

    def split_lines(self, text: str, max_chars: int) -> list[str] | None:
        return self.split_lines_with_diagnostics(text, max_chars).lines

    def split_lines_with_diagnostics(self, text: str, max_chars: int) -> SplitPlanResult:
        prompt = split_planning_prompt(text, max_chars)

        try:
            raw = self._chat(prompt)
        except Exception as exc:
            print(f"Warning: LLM split planning failed; using deterministic split. {exc}")
            return SplitPlanResult(
                lines=None,
                accepted=False,
                reject_reason="request_failed",
                input_text=text,
            )
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
                cleaned_lines=[],
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
        if _normalize_for_validation("".join(lines)) != _normalize_for_validation(text):
            return SplitPlanResult(
                lines=None,
                raw_line_count=len(raw_lines),
                clean_line_count=len(lines),
                accepted=False,
                reject_reason="normalized_text_mismatch",
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
        batch_size = max(1, int(getattr(self, "mistranscription_batch_size", 16)))
        total_batches = (len(numbered_lines) + batch_size - 1) // batch_size
        print(
            f"Final candidate review: {len(numbered_lines)} subtitles in {total_batches} batch(es).",
            flush=True,
        )
        for start in range(0, len(numbered_lines), batch_size):
            batch = numbered_lines[start : start + batch_size]
            batch_number = start // batch_size + 1
            print(
                f"Final candidate review batch {batch_number}/{total_batches}: "
                f"lines {batch[0][0]}-{batch[-1][0]}...",
                flush=True,
            )
            prompt = self._mistranscription_prompt(batch)
            try:
                raw = self._chat(prompt, max_tokens=1024)
            except Exception as exc:
                print(f"Warning: final mistranscription check failed for lines {batch[0][0]}-{batch[-1][0]}; continuing. {exc}")
                continue
            raw_blocks.append(f"=== lines {batch[0][0]}-{batch[-1][0]} ===\n{raw.strip()}")
            batch_flags = _parse_mistranscription_flags(raw, batch)
            flags.extend(batch_flags)
            print(
                f"Final candidate review batch {batch_number}/{total_batches}: "
                f"{len(batch_flags)} candidate(s).",
                flush=True,
            )

        deterministic_flags = _deterministic_mistranscription_flags(numbered_lines)
        if deterministic_flags:
            print(
                f"Final candidate review deterministic scan: {len(deterministic_flags)} candidate(s).",
                flush=True,
            )
        flags.extend(deterministic_flags)
        self.last_mistranscription_raw = "\n\n".join(raw_blocks)
        result = _dedupe_mistranscription_flags(flags)
        print(f"Final candidate review complete: {len(result)} unique candidate(s).", flush=True)
        return result

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
            raw = self._chat(prompt, max_tokens=8)
        except Exception as exc:
            print(f"Warning: boundary phrase review failed; keeping subtitle boundary. {exc}")
            return False
        decision = raw.strip().upper()
        return decision.startswith("MOVE")

    def _base_rules(self) -> str:
        # Local cleanup applies exact glossary presentation normalization after
        # validation, so the model never needs to infer aliases or substitutions.
        return cleanup_base_rules(self.mode, include_glossary=False)

    def _prompt_one(self, line: str) -> str:
        return (
            "Task:\nClean this subtitle text.\n\n"
            f"Rules:\n{self._base_rules()}\n"
            "- Output exactly one line as: 1<TAB>cleaned subtitle text.\n"
            "- If and only if the entire subtitle is filler or punctuation to remove, output: 1<TAB><DELETE>\n"
            "- Do not omit the index or output notes, bullets, or explanations.\n"
            f"\nSubtitle:\n1. {line}"
        )

    def _prompt_many(self, lines: list[str]) -> str:
        numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
        return (
            "Task:\nClean these subtitle lines.\n\n"
            f"Rules:\n{self._base_rules()}\n"
            "- Output exactly one result for every input index, in the same order.\n"
            "- Format every result as: index<TAB>cleaned subtitle text.\n"
            "- If and only if an entire subtitle is filler or punctuation to remove, use <DELETE> as its text.\n"
            "- Never omit, repeat, invent, or renumber an index.\n"
            "- Do not output notes, bullets, headings, or explanations.\n"
            f"\nLines:\n{numbered}"
        )

    def _mistranscription_prompt(self, numbered_lines: list[tuple[int, str]]) -> str:
        return mistranscription_review_prompt(numbered_lines)

    def _chat(self, prompt: str, max_tokens: int = 512) -> str:
        payload = {
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a meticulous subtitle QA reviewer. Follow the requested output format exactly."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8"))
        choice = data["choices"][0]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        completion_tokens = usage.get("completion_tokens")
        finish_reason = choice.get("finish_reason")
        self._chat_context.metadata = {
            "finish_reason": finish_reason,
            "usage": usage,
            "max_tokens": max_tokens,
            "appears_token_limited": finish_reason == "length"
            or (isinstance(completion_tokens, int) and completion_tokens >= max_tokens),
        }
        return str(choice["message"]["content"])

    def _refine_one(self, line: str) -> str | None:
        if not hasattr(self, "_chat_context"):
            self._chat_context = threading.local()
        self._chat_context.metadata = {}
        try:
            raw = self._chat(self._prompt_one(line))
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned_lines, rejection_reason = _parse_indexed_cleanup_response(raw, [line], self.glossary)
        raw_line_count = len([part for part in raw.splitlines() if part.strip()])
        if rejection_reason is not None:
            self._record_cleanup_rejection([line], raw, rejection_reason, raw_line_count, len(cleaned_lines or []))
            print(f"Warning: cleanup response rejected ({rejection_reason}); retaining original subtitle.", flush=True)
            return None
        assert cleaned_lines is not None
        return cleaned_lines[0]

    def _refine_many(self, lines: list[str]) -> list[str] | None:
        if not hasattr(self, "_chat_context"):
            self._chat_context = threading.local()
        self._chat_context.metadata = {}
        try:
            raw = self._chat(
                self._prompt_many(lines),
                max_tokens=_cleanup_max_tokens(len(lines), getattr(self, "ctx_size", 4096)),
            )
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned_lines, reason = _parse_indexed_cleanup_response(raw, lines, self.glossary)
        raw_line_count = len([part for part in raw.splitlines() if part.strip()])
        if reason is not None:
            self._record_cleanup_rejection(lines, raw, reason, raw_line_count, len(cleaned_lines or []))
            print(f"Warning: cleanup response rejected ({reason}).", flush=True)
            return None
        assert cleaned_lines is not None
        return cleaned_lines

    def _record_cleanup_rejection(
        self,
        input_lines: list[str],
        raw_response: str,
        reason: str,
        raw_nonblank_line_count: int,
        cleaned_nonblank_line_count: int,
    ) -> None:
        path = getattr(self, "cleanup_diagnostics_path", None)
        if path is None:
            return
        metadata = dict(getattr(getattr(self, "_chat_context", None), "metadata", {}) or {})
        entry = {
            "event": "local_cleanup_rejected",
            "reason": reason,
            "input_line_count": len(input_lines),
            "raw_nonblank_line_count": raw_nonblank_line_count,
            "cleaned_nonblank_line_count": cleaned_nonblank_line_count,
            "input_lines": input_lines,
            "raw_response": raw_response,
            "finish_reason": metadata.get("finish_reason"),
            "usage": metadata.get("usage", {}),
            "max_tokens": metadata.get("max_tokens"),
            "appears_token_limited": bool(metadata.get("appears_token_limited", False)),
        }
        lock = getattr(self, "_cleanup_diagnostics_lock", None)
        if lock is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return
        with lock:
            self._cleanup_diagnostics_sequence += 1
            entry["sequence"] = self._cleanup_diagnostics_sequence
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._server.close()


def _clean_response_line(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:text)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    text = re.sub(r"^\s*\d+[.)]\s*", "", text).strip()
    return text.strip().strip('"')


_CLEANUP_DELETE_MARKER = "<DELETE>"
_INDEXED_CLEANUP_LINE_RE = re.compile(r"^([0-9]+)\t(.+)$")


def _cleanup_max_tokens(line_count: int, ctx_size: int) -> int:
    """Allow indexed cleanup output to grow without consuming the full context."""
    # 512 tokens comfortably covers ordinary groups. Larger VAD groups need room
    # for the repeated indexes and line text; observed output averages stay below
    # 16 tokens per additional line. Keep at least half the context for the prompt.
    requested = 512 + max(0, line_count - 16) * 16
    context_cap = max(128, int(ctx_size) // 2)
    return min(requested, 2048, context_cap)


def _parse_indexed_cleanup_response(
    raw: str,
    originals: list[str],
    glossary: list[GlossaryEntry] | None = None,
) -> tuple[list[str] | None, str | None]:
    """Parse an indexed cleanup response atomically and fail closed."""
    raw_lines = [line.rstrip("\r") for line in raw.splitlines() if line.strip()]
    results: dict[int, str] = {}
    for raw_line in raw_lines:
        match = _INDEXED_CLEANUP_LINE_RE.fullmatch(raw_line)
        if match is None:
            return None, "malformed_indexed_line"
        index = int(match.group(1))
        if index < 1 or index > len(originals):
            return None, "out_of_range_index"
        if index in results:
            return None, "duplicate_index"
        results[index] = match.group(2).strip()

    if len(results) != len(originals) or any(index not in results for index in range(1, len(originals) + 1)):
        return None, "missing_index"
    if list(results) != list(range(1, len(originals) + 1)):
        return None, "out_of_order_index"

    cleaned_lines: list[str] = []
    for index, original in enumerate(originals, start=1):
        cleaned = results[index]
        if cleaned == _CLEANUP_DELETE_MARKER:
            if not _is_filler_only(original):
                return None, f"line_{index}_delete_non_filler"
            cleaned = ""
        elif _CLEANUP_DELETE_MARKER.casefold() in cleaned.casefold():
            return None, f"line_{index}_malformed_delete_marker"
        line_reason = _cleanup_rejection_reason(cleaned, original)
        if line_reason is not None:
            return None, f"line_{index}_{line_reason}"
        cleaned = _apply_exact_glossary_normalization(cleaned, glossary or [])
        cleaned_lines.append(cleaned)
    return cleaned_lines, None


def _split_marker_response(raw: str) -> list[str] | None:
    text = raw.strip()
    text = re.sub(r"^```(?:text)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    marker_pattern = re.compile(r"\s*(?:<\s*SPLIT\s*>|＜\s*SPLIT\s*＞|\[SPLIT\]|【SPLIT】)\s*", re.IGNORECASE)
    parts = marker_pattern.split(text)
    if len(parts) != 2:
        return None
    left = _clean_response_line(parts[0])
    right = _clean_response_line(parts[1])
    if not left or not right:
        return None
    return [left, right]


def _valid_cleaned_line(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    bad_markers = ["here is", "cleaned", "修正後", "理由", "explanation", "以下"]
    return not any(marker in lowered for marker in bad_markers)


def _valid_cleanup_result(text: str, original: str) -> bool:
    """Accept only a plausible subtitle, never a free-form model response."""
    return _cleanup_rejection_reason(text, original) is None


def _cleanup_rejection_reason(text: str, original: str) -> str | None:
    if not text:
        return None if _is_filler_only(original) else "empty_non_filler_line"
    if "\n" in text or "\r" in text:
        return "multiline_line"
    if not _valid_cleaned_line(text):
        return "explanatory_marker"
    lowered = text.casefold()
    meta_markers = (
        "thinking process",
        "analysis:",
        "reasoning:",
        "final answer:",
        "output:",
        "task:",
        "rules:",
        "glossary:",
        "思考過程",
        "考察:",
        "分析:",
        "結論:",
        "出力:",
        "ルール:",
        "用語集:",
    )
    if any(marker in lowered for marker in meta_markers):
        return "reasoning_or_prompt_marker"
    if re.search(r"(?:^|\s)(?:#{1,6}|[-*+]\s|```|\*\*|__)", text):
        return "markdown"
    # Cleanup may fix wording, but it must not turn a subtitle into a paragraph.
    if len(text) > max(len(original) * 3, len(original) + 40):
        return "implausible_expansion"
    original_substance = _strip_cleanup_fillers(original)
    cleaned_compact = _normalize_for_validation(text)
    # Cleanup may remove fillers and make small wording corrections, but losing
    # most of the actual utterance is never a safe cleanup result.
    if len(original_substance) - len(cleaned_compact) >= 8 and len(cleaned_compact) * 100 < len(original_substance) * 55:
        return "severe_contraction"
    # Cleanup is intentionally not a correction or rewriting pass. After
    # removing the fillers and punctuation it is allowed to delete, the spoken
    # content must remain identical. This catches subtle meaning changes such
    # as ません -> ます as well as glossary-driven title substitutions.
    if _cleanup_content_fingerprint(text) != _cleanup_content_fingerprint(original):
        return "semantic_content_changed"
    return None


_FILLER_ONLY_RE = re.compile(
    r"^(?:(?:えー*|あー*|うーん+|あの|その|まあ)[\s、。,.，．!！?？…・~〜～ー]*)+$"
)
_PUNCTUATION_ONLY_RE = re.compile(r"^[\s、。,.，．!！?？…・~〜～ー]*$")


def _is_filler_only(text: str) -> bool:
    stripped = text.strip()
    return bool(_FILLER_ONLY_RE.fullmatch(stripped) or _PUNCTUATION_ONLY_RE.fullmatch(stripped))


def _strip_cleanup_fillers(text: str) -> str:
    compact = _normalize_for_validation(text)
    # Short fillers such as bare え and ま are ordinary Japanese characters in
    # other contexts, so remove them only as punctuation-delimited utterances.
    # えっと is likewise treated as a filler only at an utterance boundary.
    punctuation = r"、。,.，．!！?？…・~〜～"
    compact = re.sub(
        rf"(^|[{punctuation}])(?:えっと|えー*|ま)(?=[{punctuation}]|$)",
        r"\1",
        compact,
    )
    compact = re.sub(rf"ですね(?=[{punctuation}])", "", compact)
    compact = re.sub(r"(?:えー+|あー+|うーん+|あの|その|まあ)", "", compact)
    return re.sub(rf"[{punctuation}ー]", "", compact)


def _normalize_for_validation(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _cleanup_content_fingerprint(text: str) -> str:
    return unicodedata.normalize("NFKC", _strip_cleanup_fillers(text)).casefold()


_GLOSSARY_SAFE_SEPARATOR_RE = re.compile(r"[\s・_./\\-]+")


def _apply_exact_glossary_normalization(text: str, glossary: list[GlossaryEntry]) -> str:
    """Canonicalize a glossary term without asking the model to infer aliases."""
    result = text
    for entry in glossary:
        compact = _GLOSSARY_SAFE_SEPARATOR_RE.sub("", entry.term)
        if len(compact) < 2:
            continue
        pattern = r"[\s・_./\\-]*".join(re.escape(character) for character in compact)
        if compact[0].isascii() and compact[0].isalnum():
            pattern = rf"(?<![A-Za-z0-9]){pattern}"
        if compact[-1].isascii() and compact[-1].isalnum():
            pattern = rf"{pattern}(?![A-Za-z0-9])"
        result = re.sub(pattern, lambda _match, term=entry.term: term, result, flags=re.IGNORECASE)
    return result


def _parse_mistranscription_flags(
    raw: str,
    numbered_lines: list[tuple[int, str]],
) -> list[MisTranscriptionFlag]:
    originals = {line_number: text for line_number, text in numbered_lines}
    flags: list[MisTranscriptionFlag] = []
    seen: set[tuple[int, str]] = set()
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper() == "NONE":
            continue
        match = re.match(r"^\s*(\d+)\s*(?:\t|[:：,，.)、-]+)\s*(.+?)\s*$", line)
        if not match:
            continue
        line_number = int(match.group(1))
        remainder = match.group(2)
        parts = [part.strip() for part in remainder.split("\t")]
        severity = "medium"
        explicit_severity = _severity_or_none(parts[0]) if len(parts) >= 3 else None
        if len(parts) >= 4 or explicit_severity is not None:
            severity = explicit_severity or "medium"
            flagged_text = _clean_response_line(parts[1])
            reason = _clean_response_line("\t".join(parts[2:]))
        else:
            flagged_text = _clean_response_line(parts[0])
            reason = _clean_response_line("\t".join(parts[1:])) if len(parts) > 1 else ""
        original = originals.get(line_number)
        if not original or not flagged_text:
            continue
        if flagged_text not in original:
            continue
        key = (line_number, flagged_text)
        if key in seen:
            continue
        seen.add(key)
        flags.append(MisTranscriptionFlag(line_number=line_number, text=flagged_text, reason=reason, severity=severity))
    return flags


def _severity_or_none(value: str) -> str | None:
    lowered = _clean_response_line(value).lower()
    return lowered if lowered in {"high", "medium", "low"} else None


def _deterministic_mistranscription_flags(
    numbered_lines: list[tuple[int, str]],
) -> list[MisTranscriptionFlag]:
    flags: list[MisTranscriptionFlag] = []
    artifact_markers = [
        "| prefer over",
        "full expansion of",
    ]
    for line_number, text in numbered_lines:
        for marker in artifact_markers:
            if marker in text:
                flags.append(MisTranscriptionFlag(line_number=line_number, text=text, reason="glossary artifact leaked into subtitle", severity="high"))
                break
        if _looks_like_mojibake(text):
            flags.append(MisTranscriptionFlag(line_number=line_number, text=text, reason="possible mojibake", severity="high"))
            continue
        repeated = _repeated_fragment(text)
        if repeated:
            flags.append(MisTranscriptionFlag(line_number=line_number, text=repeated, reason="repeated fragment", severity="high"))
        cut_fragment = _suspicious_cut_fragment(text)
        if cut_fragment:
            flags.append(MisTranscriptionFlag(line_number=line_number, text=cut_fragment, reason="possible cut fragment", severity="medium"))
    return flags


def _looks_like_mojibake(text: str) -> bool:
    markers = ("‚", "ƒ", "Ѓ", "Љ", "Ќ", "Џ", "‰", "–", "•", "�")
    return sum(text.count(marker) for marker in markers) >= 2


def _repeated_fragment(text: str) -> str:
    normalized = _normalize_for_validation(text)
    max_size = min(12, len(normalized) // 2)
    for size in range(max_size, 2, -1):
        for index in range(0, len(normalized) - size * 2 + 1):
            fragment = normalized[index : index + size]
            if normalized[index + size : index + size * 2] == fragment:
                return fragment * 2
    return ""


def _suspicious_cut_fragment(text: str) -> str:
    normalized = _normalize_for_validation(text)
    if not normalized:
        return ""
    suspicious_heads = ("いか", "配も", "しないか", "では", "ことで")
    suspicious_tails = ("という心", "であると", "につい", "によっ", "してい")
    for head in suspicious_heads:
        if normalized.startswith(head):
            return text[: min(len(text), max(6, len(head)))]
    for tail in suspicious_tails:
        if normalized.endswith(tail):
            return text[-min(len(text), max(6, len(tail))) :]
    return ""


def _dedupe_mistranscription_flags(flags: list[MisTranscriptionFlag]) -> list[MisTranscriptionFlag]:
    result: list[MisTranscriptionFlag] = []
    seen: set[tuple[int, str]] = set()
    for flag in flags:
        key = (flag.line_number, flag.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(flag)
    return result
