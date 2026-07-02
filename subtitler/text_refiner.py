"""Plain-text subtitle cleanup through a local llama.cpp server."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TextIO

from .errors import ModelLoadError
from .glossary import GlossaryEntry, format_glossary
from .models import ChapterSuggestion, MisTranscriptionFlag, SplitPlanResult


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
        log_path: Path | None = None,
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
        self.server_path = self._resolve_server(server_path)
        self.model_path = model_path
        self.spec_draft_model = spec_draft_model
        self.spec_draft_n_max = max(1, spec_draft_n_max)
        self.log_path = log_path
        self._log_handle: TextIO | None = None
        self.glossary = glossary
        self.mode = mode
        self.host = host
        self.port = port
        self.ctx_size = ctx_size
        self.n_gpu_layers = n_gpu_layers
        self.base_url = f"http://{host}:{port}"
        self.process: subprocess.Popen[str] | None = None
        self._owned_process = False
        self.last_mistranscription_raw = ""
        self._ensure_server()

    @staticmethod
    def _resolve_server(server_path: Path | None) -> Path:
        if server_path is not None:
            if not server_path.exists():
                raise ModelLoadError(f"llama-server not found: {server_path}")
            return server_path
        found = shutil.which("llama-server") or shutil.which("llama-server.exe")
        if found:
            return Path(found)
        common = Path(r"C:\tools\llama-vulkan\llama-server.exe")
        if common.exists():
            return common
        raise ModelLoadError(
            "llama-server was not found on PATH or at C:\\tools\\llama-vulkan\\llama-server.exe"
        )

    def _ensure_server(self) -> None:
        if self._health_ok():
            print(f"Using existing cleanup llama-server at {self.base_url}", flush=True)
            return
        gpu_layers = "all" if self.n_gpu_layers < 0 else str(self.n_gpu_layers)
        cmd = [
            str(self.server_path),
            "-m",
            str(self.model_path),
            "-ngl",
            gpu_layers,
            "-c",
            str(self.ctx_size),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--no-warmup",
            "--log-verbosity",
            "2",
            "--reasoning-budget",
            "0",
        ]
        if self.spec_draft_model is not None:
            cmd.extend(
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
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("w", encoding="utf-8")
            self._log_handle.write(" ".join(cmd) + "\n\n")
            self._log_handle.flush()
            stdout = self._log_handle
            stderr = subprocess.STDOUT
            print(f"Cleanup llama-server log: {self.log_path}", flush=True)
        else:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL
        self.process = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, text=True)
        self._owned_process = True
        deadline = time.monotonic() + 180
        next_notice = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                detail = f" See log: {self.log_path}" if self.log_path is not None else ""
                tail = _tail_log(self.log_path) if self.log_path is not None else ""
                raise ModelLoadError(
                    f"cleanup llama-server exited early with code {self.process.returncode}.{detail}{tail}"
                )
            if self._health_ok():
                print("Cleanup model ready.", flush=True)
                return
            if time.monotonic() >= next_notice:
                print("Waiting for cleanup model to load...", flush=True)
                next_notice = time.monotonic() + 10
            time.sleep(1)
        self.close()
        detail = f" See log: {self.log_path}" if self.log_path is not None else ""
        raise ModelLoadError(f"cleanup llama-server did not become healthy within 180 seconds.{detail}")

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def refine(self, lines: list[str]) -> list[str]:
        if self.mode == "off" or not lines:
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
        batch_size = 16
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
        mode_line = {
            "fillers": "Remove standalone filler sounds such as えー, あー, うーん, あの, その, and まあ when they do not add meaning.",
            "glossary": "Fix glossary terms when clearly intended.",
            "full": "Remove standalone filler sounds such as えー, あー, うーん, あの, その, and まあ when they do not add meaning, and fix glossary terms when clearly intended.",
        }.get(self.mode, "Clean the subtitle text conservatively.")
        rules = [
            "Keep the same language.",
            "Do not translate.",
            "Do not summarize.",
            mode_line,
            "Keep technical terms exactly as written in the glossary.",
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
            "Find candidate subtitle text spans a human editor may want to inspect or fix. "
            "This is non-destructive triage, so high recall is more important than precision.\n\n"
            "Rules:\n"
            "- Do not rewrite or correct the transcript.\n"
            "- Flag anything a human might reasonably review, even if it could be correct.\n"
            "- Flag likely ASR errors, odd proper nouns, broken English/product names, impossible dates or numbers, repeated fragments, partial words from subtitle cuts, unnatural particles, cleanup artifacts, mojibake, glossary leaks, and topic-incoherent wording.\n"
            "- Flag suspicious but grammatical-looking Japanese when it is awkward in context or likely from a bad split.\n"
            "- Flag short exact substrings when possible. If the suspicious part cannot be isolated, copy the whole subtitle line exactly.\n"
            "- Output one flagged item per line as: line_number<TAB>exact copied text segment<TAB>short reason\n"
            "- If you are unsure, flag it.\n"
            "- Output NONE only if there is truly nothing worth human review in this batch.\n"
            "- Do not output explanations, bullets, JSON, or extra text.\n\n"
            f"Transcript lines:\n{lines}"
        )

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
        return str(data["choices"][0]["message"]["content"])

    def _refine_one(self, line: str) -> str | None:
        try:
            raw = self._chat(self._prompt_one(line))
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned = _clean_response_line(raw)
        return cleaned if _valid_cleaned_line(cleaned) else None

    def _refine_many(self, lines: list[str]) -> list[str] | None:
        try:
            raw = self._chat(self._prompt_many(lines))
        except Exception as exc:
            print(f"Warning: cleanup failed; using original subtitle text. {exc}")
            return None
        cleaned_lines = [_clean_response_line(line) for line in raw.splitlines() if line.strip()]
        if len(cleaned_lines) != len(lines):
            return None
        if any(not _valid_cleaned_line(line) for line in cleaned_lines):
            return None
        return cleaned_lines

    def close(self) -> None:
        if self.process is None or not self._owned_process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def _tail_log(path: Path, max_chars: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = text[-max_chars:].strip()
    return f"\nLast cleanup llama-server log lines:\n{tail}" if tail else ""


def _clean_response_line(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:text)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    text = re.sub(r"^\s*\d+[.)]\s*", "", text).strip()
    return text.strip().strip('"')


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


def _normalize_for_validation(text: str) -> str:
    return re.sub(r"\s+", "", text)


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
        parts = [part.strip() for part in remainder.split("\t", 1)]
        flagged_text = _clean_response_line(parts[0])
        reason = _clean_response_line(parts[1]) if len(parts) > 1 else ""
        original = originals.get(line_number)
        if not original or not flagged_text:
            continue
        if flagged_text not in original:
            continue
        key = (line_number, flagged_text)
        if key in seen:
            continue
        seen.add(key)
        flags.append(MisTranscriptionFlag(line_number=line_number, text=flagged_text, reason=reason))
    return flags


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
                flags.append(MisTranscriptionFlag(line_number=line_number, text=text, reason="glossary artifact leaked into subtitle"))
                break
        if _looks_like_mojibake(text):
            flags.append(MisTranscriptionFlag(line_number=line_number, text=text, reason="possible mojibake"))
            continue
        repeated = _repeated_fragment(text)
        if repeated:
            flags.append(MisTranscriptionFlag(line_number=line_number, text=repeated, reason="repeated fragment"))
        cut_fragment = _suspicious_cut_fragment(text)
        if cut_fragment:
            flags.append(MisTranscriptionFlag(line_number=line_number, text=cut_fragment, reason="possible cut fragment"))
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
