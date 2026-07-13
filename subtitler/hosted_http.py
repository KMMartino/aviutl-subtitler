"""Shared HTTP policy for hosted API clients."""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any


RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_ATTEMPTS = 3
MAX_RETRY_DELAY_SEC = 60.0
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "x-api-key", "x-goog-api-key"})


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    error_type: type[Exception],
    message: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 600.0,
    malformed_error_type: type[Exception] | None = None,
    retry_exhausted_error_type: type[Exception] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    return request_json_bytes(
        method,
        url,
        body,
        error_type,
        message,
        headers=request_headers,
        timeout_sec=timeout_sec,
        malformed_error_type=malformed_error_type,
        retry_exhausted_error_type=retry_exhausted_error_type,
        attempts=attempts,
    )


def request_json_bytes(
    method: str,
    url: str,
    body: bytes | None,
    error_type: type[Exception],
    message: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 600.0,
    malformed_error_type: type[Exception] | None = None,
    retry_exhausted_error_type: type[Exception] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> dict[str, Any]:
    attempts = max(1, attempts)
    secrets = _header_secrets(headers)
    safe_message = redact_secrets(message, secrets)
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                raw = response.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise (malformed_error_type or error_type)(f"{safe_message}: malformed JSON response") from exc
            if not isinstance(data, dict):
                raise (malformed_error_type or error_type)(f"{safe_message}: expected a JSON object response")
            return data
        except urllib.error.HTTPError as exc:
            detail = redact_secrets(exc.read().decode("utf-8", errors="replace"), secrets)
            reason = f"HTTP {exc.code}"
            if exc.code not in RETRYABLE_HTTP_STATUSES:
                raise error_type(_error_message(safe_message, reason, detail)) from exc
            if attempt + 1 >= attempts:
                final_type = retry_exhausted_error_type or error_type
                raise final_type(_error_message(safe_message, reason, detail)) from exc
            delay = _retry_delay(attempt, exc.headers.get("Retry-After") if exc.headers else None)
            _print_retry_warning(safe_message, attempt, attempts, timeout_sec, reason, delay)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            detail = redact_secrets(str(exc), secrets)
            if attempt + 1 >= attempts:
                final_type = retry_exhausted_error_type or error_type
                raise final_type(_error_message(safe_message, "request error", detail)) from exc
            delay = _retry_delay(attempt, None)
            _print_retry_warning(safe_message, attempt, attempts, timeout_sec, "request error", delay)
            time.sleep(delay)
        except Exception as exc:
            if isinstance(exc, error_type) or (malformed_error_type is not None and isinstance(exc, malformed_error_type)):
                raise
            detail = redact_secrets(str(exc), secrets)
            raise error_type(_error_message(safe_message, "request error", detail)) from exc
    raise error_type(safe_message)  # pragma: no cover


def redact_secrets(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove credentials from provider errors before they reach logs or callers."""
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(r"(?i)([?&](?:key|api_key)=)[^&\s]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", redacted)
    return redacted


def _header_secrets(headers: dict[str, str] | None) -> tuple[str, ...]:
    if not headers:
        return ()
    values: list[str] = []
    for name, value in headers.items():
        if name.lower() not in _SENSITIVE_HEADER_NAMES:
            continue
        values.append(value)
        if name.lower() == "authorization" and value.lower().startswith("bearer "):
            values.append(value[7:])
    return tuple(values)


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    retry_after_sec = _parse_retry_after(retry_after)
    if retry_after_sec is not None:
        return min(MAX_RETRY_DELAY_SEC, retry_after_sec)
    base = min(MAX_RETRY_DELAY_SEC, float(2**attempt))
    return min(MAX_RETRY_DELAY_SEC, base + random.uniform(0.0, base * 0.25))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, when.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _error_message(message: str, reason: str, detail: str) -> str:
    return f"{message}: {reason}: {detail}" if detail else f"{message}: {reason}"


def _print_retry_warning(
    message: str,
    attempt: int,
    attempts: int,
    timeout_sec: float,
    reason: str,
    delay: float,
) -> None:
    print(
        f"Warning: {message}; attempt {attempt + 1}/{attempts} failed after timeout={timeout_sec:.1f}s "
        f"or retryable error ({reason}). Retrying in {delay:.2f}s...",
        flush=True,
    )
