"""Hosted API transcription backends."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .api_costs import GEMINI_AUDIO_TOKENS_PER_SECOND, OPENAI_GPT4O_TRANSCRIBE_ESTIMATED_USD_PER_MINUTE
from .api_usage import ApiUsageLedger
from .audio import write_wav_segment
from .errors import ModelLoadError, TranscriptionError
from .glossary import GlossaryEntry
from .models import AudioChunk, TranscriptChunk
from .transcriber import UNTRANSCRIBABLE_AUDIO_TOKEN, build_transcription_prompt, clean_transcript, _is_suspect_transcript
from .vad import split_chunk_with_tighter_vad


def require_api_key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ModelLoadError(f"{name} is required for this hosted API backend")
    return value


def _hosted_transcription_timeout(chunk: AudioChunk) -> float:
    duration = max(0.0, chunk.end - chunk.start)
    return min(60.0, max(15.0, duration * 2.0))


class GeminiTranscriber:
    provider = "gemini"

    def __init__(
        self,
        model: str,
        temp_dir: Path,
        usage: ApiUsageLedger,
        glossary: list[GlossaryEntry] | None = None,
        api_key: str | None = None,
        max_transcription_split_depth: int = 2,
    ) -> None:
        self.model = model
        self.temp_dir = temp_dir
        self.usage = usage
        self.api_key = api_key or require_api_key("GEMINI_API_KEY")
        self.prompt = build_transcription_prompt(glossary)
        self.max_transcription_split_depth = max(0, max_transcription_split_depth)
        verify_gemini_model_available(self.model, self.api_key)

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        return TranscriptChunk(chunk=chunk, text=self._transcribe_with_retry(chunk, depth=0))

    def _transcribe_with_retry(self, chunk: AudioChunk, depth: int) -> str:
        text = self._transcribe_once(chunk)
        if not text:
            return ""
        if not _is_external_suspect(text, chunk):
            return text
        if depth < self.max_transcription_split_depth:
            subchunks = split_chunk_with_tighter_vad(chunk, sample_rate=16000, temp_dir=self.temp_dir, keep_temp=True)
            if len(subchunks) >= 2:
                print(
                    f"Warning: suspect Gemini transcript for chunk {chunk.index} "
                    f"[{chunk.start:.2f}-{chunk.end:.2f}s]; retrying as {len(subchunks)} subchunks.",
                    flush=True,
                )
                return "".join(self._transcribe_with_retry(subchunk, depth + 1) for subchunk in subchunks)
        print(
            f"Warning: dropping suspect Gemini transcript for chunk {chunk.index} "
            f"[{chunk.start:.2f}-{chunk.end:.2f}s].",
            flush=True,
        )
        return ""

    def _transcribe_once(self, chunk: AudioChunk) -> str:
        wav_path = chunk.wav_path or self.temp_dir / f"gemini_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)
        audio_data = base64.b64encode(wav_path.read_bytes()).decode("ascii")
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.prompt},
                        {"inline_data": {"mime_type": "audio/wav", "data": audio_data}},
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.0},
        }
        data = _request_json_with_retries(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(self.model)}:generateContent"
            f"?key={urllib.parse.quote(self.api_key)}",
            payload,
            TranscriptionError,
            f"Gemini transcription failed for chunk {chunk.index}",
            timeout_sec=_hosted_transcription_timeout(chunk),
        )
        text = clean_transcript(_gemini_text(data))
        self._record_usage(data, chunk)
        if _is_untranscribable_audio_response(text):
            print(f"Warning: Gemini reported untranscribable audio for chunk {chunk.index}; skipping.", flush=True)
            return ""
        if not text:
            raise TranscriptionError(f"Gemini returned an empty transcript for chunk {chunk.index}")
        return text

    def _record_usage(self, data: dict[str, Any], chunk: AudioChunk) -> None:
        usage = data.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        total_tokens = int(usage.get("totalTokenCount") or input_tokens + output_tokens)
        audio_tokens = _gemini_audio_tokens(usage)
        if not audio_tokens:
            audio_tokens = int(round(max(0.0, chunk.end - chunk.start) * GEMINI_AUDIO_TOKENS_PER_SECOND))
        if not input_tokens:
            input_tokens = audio_tokens
        self.usage.add(
            provider=self.provider,
            model=self.model,
            operation="transcription",
            chunk_index=chunk.index,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_input_tokens=audio_tokens,
            total_tokens=total_tokens,
        )


class OpenAITranscriber:
    provider = "openai"

    def __init__(
        self,
        model: str,
        temp_dir: Path,
        usage: ApiUsageLedger,
        glossary: list[GlossaryEntry] | None = None,
        api_key: str | None = None,
        language: str = "ja",
        max_transcription_split_depth: int = 2,
    ) -> None:
        self.model = model
        self.temp_dir = temp_dir
        self.usage = usage
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
        self.prompt = build_transcription_prompt(glossary)
        self.language = language
        self.max_transcription_split_depth = max(0, max_transcription_split_depth)
        verify_openai_model_available(self.model, self.api_key)

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        return TranscriptChunk(chunk=chunk, text=self._transcribe_with_retry(chunk, depth=0))

    def _transcribe_with_retry(self, chunk: AudioChunk, depth: int) -> str:
        text = self._transcribe_once(chunk)
        if not text:
            return ""
        if not _is_external_suspect(text, chunk):
            return text
        if depth < self.max_transcription_split_depth:
            subchunks = split_chunk_with_tighter_vad(chunk, sample_rate=16000, temp_dir=self.temp_dir, keep_temp=True)
            if len(subchunks) >= 2:
                print(
                    f"Warning: suspect OpenAI transcript for chunk {chunk.index} "
                    f"[{chunk.start:.2f}-{chunk.end:.2f}s]; retrying as {len(subchunks)} subchunks.",
                    flush=True,
                )
                return "".join(self._transcribe_with_retry(subchunk, depth + 1) for subchunk in subchunks)
        print(
            f"Warning: dropping suspect OpenAI transcript for chunk {chunk.index} "
            f"[{chunk.start:.2f}-{chunk.end:.2f}s].",
            flush=True,
        )
        return ""

    def _transcribe_once(self, chunk: AudioChunk) -> str:
        wav_path = chunk.wav_path or self.temp_dir / f"openai_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)
        fields = {
            "model": self.model,
            "language": self.language,
            "prompt": self.prompt,
            "response_format": "json",
            "temperature": "0",
        }
        data = _request_multipart_with_retries(
            "https://api.openai.com/v1/audio/transcriptions",
            self.api_key,
            fields,
            "file",
            wav_path,
            TranscriptionError,
            f"OpenAI transcription failed for chunk {chunk.index}",
            timeout_sec=_hosted_transcription_timeout(chunk),
        )
        text = clean_transcript(str(data.get("text", "")))
        self._record_usage(data, chunk)
        if _is_untranscribable_audio_response(text):
            print(f"Warning: OpenAI reported untranscribable audio for chunk {chunk.index}; skipping.", flush=True)
            return ""
        if not text:
            raise TranscriptionError(f"OpenAI returned an empty transcript for chunk {chunk.index}")
        return text

    def _record_usage(self, data: dict[str, Any], chunk: AudioChunk) -> None:
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        details = usage.get("prompt_tokens_details") or usage.get("input_token_details") or {}
        audio_tokens = int(details.get("audio_tokens") or 0) if isinstance(details, dict) else 0
        fallback_cost = None
        if not (input_tokens or output_tokens or audio_tokens):
            fallback_cost = max(0.0, chunk.end - chunk.start) / 60.0 * OPENAI_GPT4O_TRANSCRIBE_ESTIMATED_USD_PER_MINUTE
        self.usage.add(
            provider=self.provider,
            model=self.model,
            operation="transcription",
            chunk_index=chunk.index,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_input_tokens=audio_tokens,
            total_tokens=total_tokens,
            cost_usd=fallback_cost,
        )


def verify_gemini_model_available(model: str, api_key: str) -> None:
    data = _request_json_with_retries(
        "GET",
        f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(api_key)}",
        None,
        ModelLoadError,
        "Could not list Gemini models",
        timeout_sec=30.0,
    )
    names = [str(item.get("name", "")).removeprefix("models/") for item in data.get("models", [])]
    if model not in names:
        flash = ", ".join(name for name in names if "flash" in name.lower()) or "none"
        raise ModelLoadError(f"Gemini model is not available: {model}. Available Flash models: {flash}")


def verify_openai_model_available(model: str, api_key: str) -> None:
    data = _request_json_with_retries(
        "GET",
        "https://api.openai.com/v1/models",
        None,
        ModelLoadError,
        "Could not list OpenAI models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout_sec=30.0,
    )
    names = [str(item.get("id", "")) for item in data.get("data", [])]
    if model not in names:
        matching = ", ".join(name for name in names if model.split("-")[0] in name or "gpt" in name) or "none"
        raise ModelLoadError(f"OpenAI model is not available: {model}. Matching models: {matching}")


def _gemini_text(data: dict[str, Any]) -> str:
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(str(part.get("text", "")) for part in parts)


def _gemini_audio_tokens(usage: dict[str, Any]) -> int:
    total = 0
    for detail in usage.get("promptTokensDetails") or []:
        if str(detail.get("modality", "")).upper() == "AUDIO":
            total += int(detail.get("tokenCount") or 0)
    return total


def _is_external_suspect(text: str, chunk: AudioChunk) -> bool:
    if _is_suspect_transcript(text, chunk):
        return True
    normalized = "".join(text.split())
    duration = max(0.0, chunk.end - chunk.start)
    if duration <= 0:
        return bool(normalized)
    if len(normalized) > max(90, duration * 22.0):
        return True
    return False


def _is_untranscribable_audio_response(text: str) -> bool:
    normalized = text.strip().strip("`").strip()
    return normalized == UNTRANSCRIBABLE_AUDIO_TOKEN


def _request_json_with_retries(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    error_type: type[Exception],
    message: str,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 600.0,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    max_attempts = 2
    for attempt in range(max_attempts):
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == max_attempts - 1:
                raise error_type(f"{message}: HTTP {exc.code}: {detail}") from exc
            _print_retry_warning(message, attempt, timeout_sec, f"HTTP {exc.code}")
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise error_type(f"{message}: {exc}") from exc
            _print_retry_warning(message, attempt, timeout_sec, str(exc))
        time.sleep(2**attempt)
    raise error_type(message)


def _request_multipart_with_retries(
    url: str,
    api_key: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    error_type: type[Exception],
    message: str,
    timeout_sec: float = 600.0,
) -> dict[str, Any]:
    max_attempts = 2
    for attempt in range(max_attempts):
        boundary = f"----subtitler-{uuid.uuid4().hex}"
        body = _multipart_body(boundary, fields, file_field, file_path)
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == max_attempts - 1:
                raise error_type(f"{message}: HTTP {exc.code}: {detail}") from exc
            _print_retry_warning(message, attempt, timeout_sec, f"HTTP {exc.code}")
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise error_type(f"{message}: {exc}") from exc
            _print_retry_warning(message, attempt, timeout_sec, str(exc))
        time.sleep(2**attempt)
    raise error_type(message)


def _print_retry_warning(message: str, attempt: int, timeout_sec: float, reason: str) -> None:
    print(
        f"Warning: {message}; attempt {attempt + 1}/2 failed after timeout={timeout_sec:.1f}s "
        f"or retryable error ({reason}). Resending request...",
        flush=True,
    )


def _multipart_body(boundary: str, fields: dict[str, str], file_field: str, file_path: Path) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("ascii"))
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode("utf-8")
    )
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)
