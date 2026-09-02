"""Hosted API transcription backends."""

from __future__ import annotations

import base64
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .api_costs import GEMINI_AUDIO_TOKENS_PER_SECOND, estimate_transcription_cost
from .api_usage import ApiUsageLedger
from .audio import write_wav_segment
from .config import openai_model_available
from .errors import ModelLoadError, TranscriptionError
from .glossary import GlossaryEntry
from .hosted_http import request_json, request_json_bytes
from .models import AlignedToken, AudioChunk, TranscriptChunk
from .model_prompts import TRANSCRIPTION_SYSTEM_PROMPT
from .transcriber import (
    UNTRANSCRIBABLE_AUDIO_TOKEN,
    _is_suspect_transcript,
    _repeats_context,
    build_transcription_prompt,
    clean_transcript,
)


def require_api_key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ModelLoadError(f"{name} is required for this hosted API backend")
    return value


def _hosted_transcription_timeout(chunk: AudioChunk, model: str = "", timeout_scale: float = 1.0) -> float:
    duration = max(0.0, chunk.end - chunk.start)
    if model.lower() == "gemini-3.7-flash":
        return max(20.0, duration * 4.0 * max(1.0, timeout_scale) + 5.0)
    model_scale = 2.0 if _is_heavy_transcription_model(model) else 1.0
    return max(5.0, duration * model_scale * max(1.0, timeout_scale))


def _is_heavy_transcription_model(model: str) -> bool:
    normalized = model.lower()
    return "pro" in normalized or normalized == "gpt-transcribe"


class MalformedTranscriptionResponse(TranscriptionError):
    """A hosted transcription endpoint returned parseable but unusable transcript data."""


class DeadTranscriptionRequest(TranscriptionError):
    """A hosted transcription request exceeded its timeout or died before a usable response."""


class FallbackTranscriber:
    def __init__(self, primary: Any, fallback: Any | None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.provider = getattr(primary, "provider", "")
        self.model = getattr(primary, "model", "")

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        if previous_transcript is None:
            return self.primary.transcribe(chunk)
        return self.primary.transcribe(chunk, previous_transcript)


class GeminiTranscriber:
    provider = "gemini"

    def __init__(
        self,
        model: str,
        temp_dir: Path,
        usage: ApiUsageLedger,
        glossary: list[GlossaryEntry] | None = None,
        api_key: str | None = None,
        timeout_scale: float = 1.0,
        allow_sparse_transcript: bool = False,
    ) -> None:
        self.model = model
        self.temp_dir = temp_dir
        self.usage = usage
        self.api_key = api_key or require_api_key("GEMINI_API_KEY")
        self.prompt = build_transcription_prompt(glossary)
        self.glossary = glossary
        self.timeout_scale = max(1.0, timeout_scale)
        self.allow_sparse_transcript = allow_sparse_transcript
        verify_gemini_model_available(self.model, self.api_key)

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        text = self._transcribe_once(chunk, previous_transcript)
        return TranscriptChunk(chunk=chunk, text=text)

    def _transcribe_once(self, chunk: AudioChunk, previous_transcript: str | None = None) -> str:
        wav_path = chunk.wav_path or self.temp_dir / f"gemini_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)
        audio_data = base64.b64encode(wav_path.read_bytes()).decode("ascii")
        payload = {
            "systemInstruction": {"parts": [{"text": TRANSCRIPTION_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": build_transcription_prompt(self.glossary, previous_transcript)},
                        {"inline_data": {"mime_type": "audio/wav", "data": audio_data}},
                    ],
                }
            ],
        }
        if self.model == "gemini-3.7-flash":
            payload["generationConfig"] = {"thinkingConfig": {"thinkingLevel": "low"}}
        if not self.model.startswith("gemini-3"):
            payload["generationConfig"] = {"temperature": 0.0}
        data = _request_json(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(self.model)}:generateContent",
            payload,
            TranscriptionError,
            f"Gemini transcription failed for chunk {chunk.index}",
            timeout_sec=_hosted_transcription_timeout(chunk, self.model, self.timeout_scale),
            headers={"x-goog-api-key": self.api_key},
            malformed_error_type=MalformedTranscriptionResponse,
            dead_request_error_type=DeadTranscriptionRequest,
        )
        text = clean_transcript(_gemini_text(data))
        self._record_usage(data, chunk)
        if _is_untranscribable_audio_response(text):
            print(f"Warning: Gemini reported untranscribable audio for chunk {chunk.index}; skipping.", flush=True)
            return ""
        if not text:
            raise MalformedTranscriptionResponse(f"Gemini returned an empty transcript for chunk {chunk.index}")
        if _is_external_suspect(text, chunk, allow_sparse=self.allow_sparse_transcript):
            raise MalformedTranscriptionResponse(f"Gemini returned a suspect transcript for chunk {chunk.index}")
        if previous_transcript and _repeats_context(text, previous_transcript):
            raise MalformedTranscriptionResponse(f"Gemini repeated preceding context for chunk {chunk.index}")
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


class Gemini35TranscribeAdapter(GeminiTranscriber):
    """Experimental adapter for Gemini's dedicated Files + Interactions transcription API."""

    def __init__(self, *args: Any, language: str = "ja", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.language = language
        self.native_timestamps = os.environ.get("GEMINI_35_NATIVE_TIMESTAMPS") == "1"
        self._native_tokens: dict[int, list[AlignedToken]] = {}
        self._native_tokens_lock = threading.Lock()

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        text = self._transcribe_once(chunk, previous_transcript)
        with self._native_tokens_lock:
            tokens = self._native_tokens.pop(chunk.index, [])
        return TranscriptChunk(chunk=chunk, text=text, tokens=tokens)

    def _transcribe_once(self, chunk: AudioChunk, previous_transcript: str | None = None) -> str:
        wav_path = chunk.wav_path or self.temp_dir / f"gemini35_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)
        file_name = _upload_gemini_file(wav_path, self.api_key, chunk.index, self.timeout_scale)
        try:
            transcription_config = {
                "language_codes": [_gemini_language_code(self.language)],
                "mode": (
                    {"type": "verbatim", "timestamp_granularities": ["word"]}
                    if self.native_timestamps
                    else {"type": "verbatim"}
                ),
            }
            # The Interactions API currently rejects custom vocabulary combined
            # with timestamp annotations.  Native-timestamp experiments must
            # therefore assess timing independently of glossary support.
            if not self.native_timestamps:
                transcription_config["custom_vocabulary"] = [
                    entry.term for entry in self.glossary or [] if entry.term.strip()
                ][:100]
            payload = {
                "model": self.model,
                "input": [{"type": "audio", "uri": file_name, "mime_type": "audio/wav"}],
                "generation_config": {
                    "transcription_config": transcription_config
                },
            }
            data = _request_json(
                "POST",
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                payload,
                TranscriptionError,
                f"Gemini 3.5 Transcribe failed for chunk {chunk.index}",
                timeout_sec=_hosted_transcription_timeout(chunk, self.model, self.timeout_scale),
                headers={"x-goog-api-key": self.api_key},
                malformed_error_type=MalformedTranscriptionResponse,
                dead_request_error_type=DeadTranscriptionRequest,
            )
        finally:
            _delete_gemini_file(file_name, self.api_key)
        text = clean_transcript(_gemini_interaction_text(data))
        if self.native_timestamps:
            tokens = _gemini_interaction_tokens(data, chunk)
            if not tokens:
                raise MalformedTranscriptionResponse(
                    f"Gemini 3.5 Transcribe returned no word timestamps for chunk {chunk.index}"
                )
            with self._native_tokens_lock:
                self._native_tokens[chunk.index] = tokens
        self._record_interaction_usage(data, chunk)
        if _is_untranscribable_audio_response(text):
            print(f"Warning: Gemini reported untranscribable audio for chunk {chunk.index}; skipping.", flush=True)
            return ""
        if not text:
            raise MalformedTranscriptionResponse(f"Gemini 3.5 Transcribe returned an empty transcript for chunk {chunk.index}")
        if _is_external_suspect(text, chunk, allow_sparse=self.allow_sparse_transcript):
            raise MalformedTranscriptionResponse(f"Gemini 3.5 Transcribe returned a suspect transcript for chunk {chunk.index}")
        return text

    def _record_interaction_usage(self, data: dict[str, Any], chunk: AudioChunk) -> None:
        usage = data.get("usageMetadata") or data.get("usage") or {}
        input_tokens = int(usage.get("promptTokenCount") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("totalTokenCount") or usage.get("total_tokens") or input_tokens + output_tokens)
        audio_tokens = _gemini_audio_tokens(usage)
        if not audio_tokens:
            audio_tokens = int(round(max(0.0, chunk.end - chunk.start) * GEMINI_AUDIO_TOKENS_PER_SECOND))
        if not input_tokens:
            input_tokens = audio_tokens
        fallback_cost = None
        if not output_tokens:
            fallback_cost = estimate_transcription_cost(self.provider, self.model, max(0.0, chunk.end - chunk.start))
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


def _gemini_language_code(language: str) -> str:
    return {"ja": "ja-JP", "en": "en-US", "ko": "ko-KR", "zh": "zh-CN"}.get(language.lower(), language)


def _gemini_interaction_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    for step in data.get("steps") or []:
        for content in step.get("content") or []:
            if content.get("type") == "text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def _gemini_interaction_tokens(data: dict[str, Any], chunk: AudioChunk) -> list[AlignedToken]:
    tokens: list[AlignedToken] = []
    for step in data.get("steps") or []:
        for content in step.get("content") or []:
            for annotation in content.get("annotations") or []:
                if annotation.get("type") != "word_info":
                    continue
                text = str(annotation.get("text") or "").strip()
                start = _gemini_offset_seconds(annotation.get("start_offset"))
                end = _gemini_offset_seconds(annotation.get("end_offset"))
                if text and start is not None and end is not None and end > start:
                    tokens.append(AlignedToken(text, chunk.start + start, chunk.start + end, "word"))
    return tokens


def _gemini_offset_seconds(value: Any) -> float | None:
    try:
        return float(str(value).removesuffix("s"))
    except (TypeError, ValueError):
        return None


def _upload_gemini_file(wav_path: Path, api_key: str, chunk_index: int, timeout_scale: float) -> str:
    body = json.dumps({"file": {"display_name": f"subtitler-gemini35-{chunk_index:05d}"}}).encode("utf-8")
    start = urllib.request.Request(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        data=body,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(wav_path.stat().st_size),
            "X-Goog-Upload-Header-Content-Type": "audio/wav",
        },
        method="POST",
    )
    timeout_sec = max(30.0, 60.0 * max(1.0, timeout_scale))
    try:
        with urllib.request.urlopen(start, timeout=timeout_sec) as response:
            upload_url = response.headers.get("x-goog-upload-url")
        if not upload_url:
            raise MalformedTranscriptionResponse("Gemini Files API did not return an upload URL")
        upload = urllib.request.Request(
            upload_url,
            data=wav_path.read_bytes(),
            headers={
                "Content-Length": str(wav_path.stat().st_size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            method="POST",
        )
        with urllib.request.urlopen(upload, timeout=timeout_sec) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise TranscriptionError(f"Gemini Files API upload failed for chunk {chunk_index}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeadTranscriptionRequest(f"Gemini Files API upload failed for chunk {chunk_index}") from exc
    uri = str((data.get("file") or {}).get("uri") or "")
    if not uri:
        raise MalformedTranscriptionResponse(f"Gemini Files API returned no URI for chunk {chunk_index}")
    return uri


def _delete_gemini_file(file_uri: str, api_key: str) -> None:
    name = file_uri.removeprefix("https://generativelanguage.googleapis.com/v1beta/")
    if not name.startswith("files/"):
        return
    try:
        _request_json(
            "DELETE",
            f"https://generativelanguage.googleapis.com/v1beta/{name}",
            None,
            TranscriptionError,
            "Gemini Files API cleanup failed",
            headers={"x-goog-api-key": api_key},
            timeout_sec=30.0,
        )
    except TranscriptionError:
        print("Warning: could not delete a temporary Gemini transcription upload.", flush=True)


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
        timeout_scale: float = 1.0,
        allow_sparse_transcript: bool = False,
    ) -> None:
        self.model = model
        self.temp_dir = temp_dir
        self.usage = usage
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
        self.prompt = build_transcription_prompt(glossary)
        self.glossary = glossary
        self.language = language
        self.timeout_scale = max(1.0, timeout_scale)
        self.allow_sparse_transcript = allow_sparse_transcript
        verify_openai_model_available(self.model, self.api_key)

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        text = self._transcribe_once(chunk, previous_transcript)
        return TranscriptChunk(chunk=chunk, text=text)

    def _transcribe_once(self, chunk: AudioChunk, previous_transcript: str | None = None) -> str:
        wav_path = chunk.wav_path or self.temp_dir / f"openai_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)
        fields = self._transcription_fields(previous_transcript)
        data = self._request_transcription(chunk, wav_path, fields)
        text = clean_transcript(str(data.get("text", "")))
        self._record_usage(data, chunk)
        if _is_untranscribable_audio_response(text):
            print(f"Warning: OpenAI reported untranscribable audio for chunk {chunk.index}; skipping.", flush=True)
            return ""
        if not text:
            raise MalformedTranscriptionResponse(f"OpenAI returned an empty transcript for chunk {chunk.index}")
        if _is_external_suspect(text, chunk, allow_sparse=self.allow_sparse_transcript):
            raise MalformedTranscriptionResponse(f"OpenAI returned a suspect transcript for chunk {chunk.index}")
        if previous_transcript and _repeats_context(text, previous_transcript):
            raise MalformedTranscriptionResponse(f"OpenAI repeated preceding context for chunk {chunk.index}")
        return text

    def _transcription_fields(self, previous_transcript: str | None) -> dict[str, str | list[str]]:
        return {
            "model": self.model,
            "language": self.language,
            "prompt": build_transcription_prompt(self.glossary, previous_transcript),
            "response_format": "json",
            "temperature": "0",
        }

    def _request_transcription(
        self,
        chunk: AudioChunk,
        wav_path: Path,
        fields: dict[str, str | list[str]],
    ) -> dict[str, Any]:
        return _request_multipart(
            "https://api.openai.com/v1/audio/transcriptions",
            self.api_key,
            fields,
            "file",
            wav_path,
            TranscriptionError,
            f"OpenAI transcription failed for chunk {chunk.index}",
            timeout_sec=_hosted_transcription_timeout(chunk, str(fields.get("model") or self.model), self.timeout_scale),
            malformed_error_type=MalformedTranscriptionResponse,
            dead_request_error_type=DeadTranscriptionRequest,
        )

    def _record_usage(self, data: dict[str, Any], chunk: AudioChunk, model: str | None = None) -> None:
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        details = usage.get("prompt_tokens_details") or usage.get("input_token_details") or {}
        audio_tokens = int(details.get("audio_tokens") or 0) if isinstance(details, dict) else 0
        fallback_cost = None
        if not (input_tokens or output_tokens or audio_tokens):
            fallback_cost = estimate_transcription_cost(
                self.provider,
                model or self.model,
                max(0.0, chunk.end - chunk.start),
            )
        self.usage.add(
            provider=self.provider,
            model=model or self.model,
            operation="transcription",
            chunk_index=chunk.index,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_input_tokens=audio_tokens,
            total_tokens=total_tokens,
            cost_usd=fallback_cost,
        )


class GPTTranscribeAdapter(OpenAITranscriber):
    """Adapt the shared OpenAI file-transcription client to GPT Transcribe fields."""

    def _transcription_fields(self, previous_transcript: str | None) -> dict[str, str | list[str]]:
        fields: dict[str, str | list[str]] = {
            "model": self.model,
            "response_format": "json",
        }
        if self.language:
            fields["languages"] = [self.language]
        keywords = [
            entry.term.strip()
            for entry in self.glossary or []
            if _valid_gpt_transcribe_keyword(entry.term)
        ]
        if keywords:
            fields["keywords"] = keywords
        if previous_transcript:
            fields["prompt"] = previous_transcript
        return fields


def _valid_gpt_transcribe_keyword(value: str) -> bool:
    keyword = value.strip()
    return bool(keyword) and not any(character in keyword for character in ("<", ">", "\r", "\n"))


def verify_gemini_model_available(model: str, api_key: str) -> None:
    data = _request_json(
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/models",
        None,
        ModelLoadError,
        "Could not list Gemini models",
        headers={"x-goog-api-key": api_key},
        timeout_sec=30.0,
    )
    names = [str(item.get("name", "")).removeprefix("models/") for item in data.get("models", [])]
    if model not in names:
        flash = ", ".join(name for name in names if "flash" in name.lower()) or "none"
        raise ModelLoadError(f"Gemini model is not available: {model}. Available Flash models: {flash}")


def verify_openai_model_available(model: str, api_key: str) -> str:
    data = _request_json(
        "GET",
        "https://api.openai.com/v1/models",
        None,
        ModelLoadError,
        "Could not list OpenAI models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout_sec=30.0,
    )
    names = [str(item.get("id", "")) for item in data.get("data", [])]
    available = openai_model_available(model, names)
    if available is None:
        matching = ", ".join(name for name in names if model.split("-")[0] in name or "gpt" in name) or "none"
        raise ModelLoadError(f"OpenAI model is not available: {model}. Matching models: {matching}")
    return available


def _gemini_text(data: dict[str, Any]) -> str:
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(str(part.get("text", "")) for part in parts)


def _gemini_audio_tokens(usage: dict[str, Any]) -> int:
    total = 0
    for detail in usage.get("promptTokensDetails") or []:
        if str(detail.get("modality", "")).upper() == "AUDIO":
            total += int(detail.get("tokenCount") or 0)
    return total


def _is_external_suspect(text: str, chunk: AudioChunk, *, allow_sparse: bool = False) -> bool:
    if _is_suspect_transcript(text, chunk, enforce_minimum_density=not allow_sparse):
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


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    error_type: type[Exception],
    message: str,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 600.0,
    malformed_error_type: type[Exception] | None = None,
    dead_request_error_type: type[Exception] | None = None,
) -> dict[str, Any]:
    return request_json(
        method,
        url,
        payload,
        error_type,
        message,
        headers=headers,
        timeout_sec=timeout_sec,
        malformed_error_type=malformed_error_type,
        retry_exhausted_error_type=dead_request_error_type,
    )


def _request_multipart(
    url: str,
    api_key: str,
    fields: dict[str, str | list[str]],
    file_field: str,
    file_path: Path,
    error_type: type[Exception],
    message: str,
    timeout_sec: float = 600.0,
    malformed_error_type: type[Exception] | None = None,
    dead_request_error_type: type[Exception] | None = None,
) -> dict[str, Any]:
    boundary = f"----subtitler-{uuid.uuid4().hex}"
    body = _multipart_body(boundary, fields, file_field, file_path)
    return request_json_bytes(
        "POST",
        url,
        body,
        error_type,
        message,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        timeout_sec=timeout_sec,
        malformed_error_type=malformed_error_type,
        retry_exhausted_error_type=dead_request_error_type,
    )


def _multipart_body(
    boundary: str,
    fields: dict[str, str | list[str]],
    file_field: str,
    file_path: Path,
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        field_name = f"{name}[]" if isinstance(value, list) else name
        for item in values:
            chunks.append(f"--{boundary}\r\n".encode("ascii"))
            chunks.append(f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode("utf-8"))
            chunks.append(str(item).encode("utf-8"))
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
