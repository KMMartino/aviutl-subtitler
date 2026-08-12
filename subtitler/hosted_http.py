"""Shared HTTP policy for hosted API clients."""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable


RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_ATTEMPTS = 3
MAX_RETRY_DELAY_SEC = 60.0
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "x-api-key", "x-goog-api-key"})
AttemptObserver = Callable[[dict[str, Any]], None]


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
    attempt_observer: AttemptObserver | None = None,
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
        attempt_observer=attempt_observer,
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
    attempt_observer: AttemptObserver | None = None,
) -> dict[str, Any]:
    attempts = max(1, attempts)
    secrets = _header_secrets(headers)
    safe_message = redact_secrets(message, secrets)
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                raw = response.read().decode("utf-8")
                response_headers = getattr(response, "headers", None)
            elapsed = time.monotonic() - started
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                _notify_attempt(
                    attempt_observer,
                    _attempt_event(
                        attempt,
                        attempts,
                        timeout_sec,
                        started_at,
                        elapsed,
                        outcome="response_error",
                        error_category="malformed_json",
                        error_detail="Response was not valid JSON",
                        request_id=_request_id(response_headers),
                    ),
                )
                raise (malformed_error_type or error_type)(f"{safe_message}: malformed JSON response") from exc
            if not isinstance(data, dict):
                _notify_attempt(
                    attempt_observer,
                    _attempt_event(
                        attempt,
                        attempts,
                        timeout_sec,
                        started_at,
                        elapsed,
                        outcome="response_error",
                        error_category="unexpected_response_shape",
                        error_detail="Response was not a JSON object",
                        request_id=_request_id(response_headers),
                    ),
                )
                raise (malformed_error_type or error_type)(f"{safe_message}: expected a JSON object response")
            _notify_attempt(
                attempt_observer,
                _attempt_event(
                    attempt,
                    attempts,
                    timeout_sec,
                    started_at,
                    elapsed,
                    outcome="success",
                    request_id=_request_id(response_headers),
                ),
            )
            return data
        except urllib.error.HTTPError as exc:
            elapsed = time.monotonic() - started
            detail = redact_secrets(exc.read().decode("utf-8", errors="replace"), secrets)
            reason = f"HTTP {exc.code}"
            if exc.code not in RETRYABLE_HTTP_STATUSES:
                _notify_attempt(
                    attempt_observer,
                    _attempt_event(
                        attempt,
                        attempts,
                        timeout_sec,
                        started_at,
                        elapsed,
                        outcome="http_error",
                        http_status=exc.code,
                        error_category="http_status",
                        error_detail=str(exc.reason or reason),
                        request_id=_request_id(exc.headers),
                    ),
                )
                raise error_type(_error_message(safe_message, reason, detail)) from exc
            if attempt + 1 >= attempts:
                _notify_attempt(
                    attempt_observer,
                    _attempt_event(
                        attempt,
                        attempts,
                        timeout_sec,
                        started_at,
                        elapsed,
                        outcome="http_error",
                        http_status=exc.code,
                        error_category="http_status",
                        error_detail=str(exc.reason or reason),
                        request_id=_request_id(exc.headers),
                    ),
                )
                final_type = retry_exhausted_error_type or error_type
                raise final_type(_error_message(safe_message, reason, detail)) from exc
            delay = _retry_delay(attempt, exc.headers.get("Retry-After") if exc.headers else None)
            _notify_attempt(
                attempt_observer,
                _attempt_event(
                    attempt,
                    attempts,
                    timeout_sec,
                    started_at,
                    elapsed,
                    outcome="http_error",
                    http_status=exc.code,
                    error_category="http_status",
                    error_detail=str(exc.reason or reason),
                    request_id=_request_id(exc.headers),
                    retry_delay_sec=delay,
                ),
            )
            _print_retry_warning(safe_message, attempt, attempts, timeout_sec, reason, elapsed, delay)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            elapsed = time.monotonic() - started
            detail = redact_secrets(str(exc), secrets)
            category = "timeout" if _is_timeout_error(exc) else "request_error"
            reason = "timeout" if category == "timeout" else "request error"
            if attempt + 1 >= attempts:
                _notify_attempt(
                    attempt_observer,
                    _attempt_event(
                        attempt,
                        attempts,
                        timeout_sec,
                        started_at,
                        elapsed,
                        outcome=category,
                        error_category=category,
                        error_detail=_diagnostic_detail(detail),
                    ),
                )
                final_type = retry_exhausted_error_type or error_type
                raise final_type(_error_message(safe_message, reason, detail)) from exc
            delay = _retry_delay(attempt, None)
            _notify_attempt(
                attempt_observer,
                _attempt_event(
                    attempt,
                    attempts,
                    timeout_sec,
                    started_at,
                    elapsed,
                    outcome=category,
                    error_category=category,
                    error_detail=_diagnostic_detail(detail),
                    retry_delay_sec=delay,
                ),
            )
            _print_retry_warning(safe_message, attempt, attempts, timeout_sec, reason, elapsed, delay)
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
    elapsed_sec: float,
    delay: float,
) -> None:
    if reason == "timeout":
        failure = f"timed out after {elapsed_sec:.2f}s"
    elif reason.startswith("HTTP "):
        failure = f"received {reason} after {elapsed_sec:.2f}s"
    else:
        failure = f"failed with {reason} after {elapsed_sec:.2f}s"
    print(
        f"Warning: {message}; attempt {attempt + 1}/{attempts} {failure}. "
        f"Timeout limit: {timeout_sec:.1f}s. Retrying in {delay:.2f}s...",
        flush=True,
    )


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)


def _diagnostic_detail(detail: str) -> str:
    return " ".join(detail.split())[:500]


def _request_id(headers: Any) -> str | None:
    if headers is None:
        return None
    for name in ("x-request-id", "request-id", "x-goog-request-id", "x-cloud-trace-context"):
        value = headers.get(name)
        if value:
            return _diagnostic_detail(str(value))[:200]
    return None


def _attempt_event(
    attempt: int,
    attempts: int,
    timeout_sec: float,
    started_at: str,
    elapsed_sec: float,
    *,
    outcome: str,
    http_status: int | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    request_id: str | None = None,
    retry_delay_sec: float | None = None,
) -> dict[str, Any]:
    return {
        "request_attempt": attempt + 1,
        "request_attempts_allowed": attempts,
        "started_at": started_at,
        "elapsed_sec": round(elapsed_sec, 3),
        "timeout_sec": timeout_sec,
        "outcome": outcome,
        "http_status": http_status,
        "error_category": error_category,
        "error_detail": error_detail,
        "request_id": request_id,
        "retry_delay_sec": retry_delay_sec,
    }


def _notify_attempt(observer: AttemptObserver | None, event: dict[str, Any]) -> None:
    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        # Diagnostics must never affect the request itself.
        return
