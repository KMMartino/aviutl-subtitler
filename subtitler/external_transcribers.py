"""Hosted API transcription backends."""

from __future__ import annotations

import base64
import json
import os
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


def require_api_key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ModelLoadError(f"{name} is required for this hosted API backend")
    return value


def _hosted_transcription_timeout(chunk: AudioChunk, model: str = "", timeout_scale: float = 1.0) -> float:
    duration = max(0.0, chunk.end - chunk.start)
    model_scale = 2.0 if _is_heavy_transcription_model(model) else 1.0
    return max(5.0, duration * model_scale * max(1.0, timeout_scale))


def _is_heavy_transcription_model(model: str) -> bool:
    normalized = model.lower()
    return "pro" in normalized or normalized == "gpt-4o-transcribe"


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

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        try:
            return self.primary.transcribe(chunk)
        except (MalformedTranscriptionResponse, DeadTranscriptionRequest) as exc:
            if self.fallback is None:
                raise
            reason = "dead request" if isinstance(exc, DeadTranscriptionRequest) else "malformed response"
            print(
                f"Warning: {reason} from {self.primary.provider} transcription for chunk {chunk.index}; "
                f"falling back to {self.fallback.provider}:{self.fallback.model}. {exc}",
                flush=True,
            )
            return self.fallback.transcribe(chunk)


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
    ) -> None:
        self.model = model
        self.temp_dir = temp_dir
        self.usage = usage
        self.api_key = api_key or require_api_key("GEMINI_API_KEY")
        self.prompt = build_transcription_prompt(glossary)
        self.timeout_scale = max(1.0, timeout_scale)
        verify_gemini_model_available(self.model, self.api_key)

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        text = self._transcribe_once(chunk)
        return TranscriptChunk(chunk=chunk, text=text)

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
        data = _request_json(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(self.model)}:generateContent"
            f"?key={urllib.parse.quote(self.api_key)}",
            payload,
            TranscriptionError,
            f"Gemini transcription failed for chunk {chunk.index}",
            timeout_sec=_hosted_transcription_timeout(chunk, self.model, self.timeout_scale),
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
        if _is_external_suspect(text, chunk):
            raise MalformedTranscriptionResponse(f"Gemini returned a suspect transcript for chunk {chunk.index}")
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
        timeout_scale: float = 1.0,
    ) -> None:
        self.model = model
        self.temp_dir = temp_dir
        self.usage = usage
        self.api_key = api_key or require_api_key("OPENAI_API_KEY")
        self.prompt = build_transcription_prompt(glossary)
        self.language = language
        self.timeout_scale = max(1.0, timeout_scale)
        verify_openai_model_available(self.model, self.api_key)

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        text = self._transcribe_once(chunk)
        return TranscriptChunk(chunk=chunk, text=text)

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
        data = _request_multipart(
            "https://api.openai.com/v1/audio/transcriptions",
            self.api_key,
            fields,
            "file",
            wav_path,
            TranscriptionError,
            f"OpenAI transcription failed for chunk {chunk.index}",
            timeout_sec=_hosted_transcription_timeout(chunk, self.model, self.timeout_scale),
            malformed_error_type=MalformedTranscriptionResponse,
            dead_request_error_type=DeadTranscriptionRequest,
        )
        text = clean_transcript(str(data.get("text", "")))
        self._record_usage(data, chunk)
        if _is_untranscribable_audio_response(text):
            print(f"Warning: OpenAI reported untranscribable audio for chunk {chunk.index}; skipping.", flush=True)
            return ""
        if not text:
            raise MalformedTranscriptionResponse(f"OpenAI returned an empty transcript for chunk {chunk.index}")
        if _is_external_suspect(text, chunk):
            raise MalformedTranscriptionResponse(f"OpenAI returned a suspect transcript for chunk {chunk.index}")
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
    data = _request_json(
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
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise (malformed_error_type or error_type)(f"{message}: malformed JSON response") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if dead_request_error_type is not None and exc.code in {408, 504}:
            raise dead_request_error_type(f"{message}: HTTP {exc.code}: {detail}") from exc
        raise error_type(f"{message}: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        if dead_request_error_type is not None and _is_dead_request_exception(exc):
            raise dead_request_error_type(f"{message}: {exc}") from exc
        raise error_type(f"{message}: {exc}") from exc


def _request_multipart(
    url: str,
    api_key: str,
    fields: dict[str, str],
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
    except json.JSONDecodeError as exc:
        raise (malformed_error_type or error_type)(f"{message}: malformed JSON response") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if dead_request_error_type is not None and exc.code in {408, 504}:
            raise dead_request_error_type(f"{message}: HTTP {exc.code}: {detail}") from exc
        raise error_type(f"{message}: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        if dead_request_error_type is not None and _is_dead_request_exception(exc):
            raise dead_request_error_type(f"{message}: {exc}") from exc
        raise error_type(f"{message}: {exc}") from exc


def _is_dead_request_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text


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
