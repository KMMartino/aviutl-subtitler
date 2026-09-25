"""Alibaba Model Studio transcription and subtitle text clients."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .api_usage import ApiUsageLedger
from .audio import write_wav_segment
from .errors import ModelLoadError, StructuredOutputIncompleteError, TranscriptionError
from .external_refiners import HostedTextRefiner, _openai_system_prompt
from .external_transcribers import require_api_key, _is_external_suspect
from .glossary import GlossaryEntry
from .hosted_http import request_json
from .models import AudioChunk, TranscriptChunk
from .transcriber import clean_transcript, _repeats_context


def qwen_base_url() -> str:
    base = (os.environ.get("DASHSCOPE_BASE_URL", "").strip() or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelLoadError("DASHSCOPE_BASE_URL must be an HTTPS OpenAI-compatible endpoint")
    if not base.endswith("/compatible-mode/v1"):
        raise ModelLoadError("DASHSCOPE_BASE_URL must end in /compatible-mode/v1")
    return base


class QwenTranscriber:
    provider = "dashscope"

    def __init__(self, model: str, temp_dir: Path, usage: ApiUsageLedger,
                 glossary: list[GlossaryEntry] | None = None, language: str = "ja", *, allow_sparse_transcript: bool = False):
        self.model, self.temp_dir, self.usage = model, temp_dir, usage
        self.glossary, self.language = glossary or [], language
        self.allow_sparse_transcript = allow_sparse_transcript
        self.base_url = qwen_base_url()
        self.api_key = require_api_key("DASHSCOPE_API_KEY")

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        if chunk.end - chunk.start > 300:
            raise TranscriptionError("Qwen ASR accepts at most five minutes per request")
        wav = chunk.wav_path or self.temp_dir / f"qwen_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav)
        messages: list[dict[str, Any]] = []
        if previous_transcript:
            messages.append({"role": "user", "content": [{"type": "input_text", "text": previous_transcript[-400:]}]})
        messages.append({"role": "user", "content": [{"type": "input_audio", "input_audio": {
            "data": "data:audio/wav;base64," + base64.b64encode(wav.read_bytes()).decode("ascii")}}]})
        parameters: dict[str, Any] = {"format": "wav", "sample_rate": "16000"}
        if self.language and self.language != "auto":
            parameters["language_hints"] = [self.language]
        if self.glossary:
            parameters["vocabulary"] = {entry.term: 1 for entry in self.glossary}
        data = request_json("POST", self.base_url.removesuffix("/compatible-mode/v1") +
                            "/api/v1/services/aigc/multimodal-generation/generation",
                            {"model": self.model, "input": {"messages": messages}, "parameters": parameters},
                            TranscriptionError, "Qwen ASR request failed", timeout_sec=600,
                            headers={"Authorization": f"Bearer {self.api_key}", "X-DashScope-SSE": "disable"})
        usage = data.get("usage") or {}
        self.usage.add(provider=self.provider, model=self.model, operation="transcription", chunk_index=chunk.index,
                       input_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)))
        output = data.get("output") or {}
        text = output.get("text") or (output.get("sentence") or {}).get("text") or (output.get("output") or {}).get("sentence", {}).get("text")
        if not isinstance(text, str) or not text.strip():
            raise TranscriptionError("Qwen ASR returned no transcript")
        text = clean_transcript(text)
        if _is_external_suspect(text, chunk, allow_sparse=self.allow_sparse_transcript):
            raise TranscriptionError("Qwen ASR returned a suspect transcript")
        if previous_transcript and _repeats_context(text, previous_transcript):
            raise TranscriptionError("Qwen ASR repeated the preceding transcript")
        return TranscriptChunk(chunk, text)


class QwenTextRefiner(HostedTextRefiner):
    provider = "dashscope"

    def __init__(self, model: str, glossary: list[GlossaryEntry], usage: ApiUsageLedger):
        super().__init__(model, glossary, usage)
        self.base_url = qwen_base_url()
        self.api_key = require_api_key("DASHSCOPE_API_KEY")

    def _chat(self, prompt: str, max_tokens: int = 512, operation: str = "cleanup",
              attempt_observer: Callable[[dict[str, Any]], None] | None = None,
              response_schema: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {"model": self.model, "enable_thinking": False, "temperature": 0,
            "max_tokens": max_tokens, "messages": [{"role": "system", "content": _openai_system_prompt(operation)},
                                                   {"role": "user", "content": prompt}]}
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
            payload["messages"][1]["content"] += "\nReturn JSON matching this schema:\n" + json.dumps(response_schema)
        data = request_json("POST", self.base_url + "/chat/completions", payload, ModelLoadError,
                            "Qwen text request failed", timeout_sec=600, attempt_observer=attempt_observer,
                            headers={"Authorization": f"Bearer {self.api_key}"})
        usage = data.get("usage") or {}
        self.usage.add(provider=self.provider, model=self.model, operation=operation,
                       input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)))
        choice = data.get("choices", [{}])[0]
        if choice.get("finish_reason") != "stop":
            raise StructuredOutputIncompleteError("Qwen response was incomplete", reason=str(choice.get("finish_reason")))
        content = choice.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelLoadError("Qwen returned no text")
        return content
