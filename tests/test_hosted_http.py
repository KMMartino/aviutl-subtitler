import io
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from unittest import mock

from subtitler.errors import ModelLoadError, TranscriptionError
from subtitler.external_transcribers import DeadTranscriptionRequest, FallbackTranscriber, _request_json
from subtitler.hosted_http import request_json
from subtitler.models import AudioChunk, TranscriptChunk


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


def _http_error(code: int, detail: str = "failure", retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.test", code, "failure", headers, io.BytesIO(detail.encode()))


class HostedHttpTests(unittest.TestCase):
    @mock.patch("subtitler.hosted_http.random.uniform", return_value=0.25)
    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen")
    def test_retryable_http_uses_exponential_backoff_with_jitter(self, urlopen, sleep, _uniform) -> None:
        urlopen.side_effect = [_http_error(500), _http_error(503), _Response(b'{"ok": true}')]

        result = request_json("GET", "https://example.test", None, ModelLoadError, "failed")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.25, 2.25])

    @mock.patch("subtitler.hosted_http.random.uniform")
    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen")
    def test_retry_after_header_takes_precedence(self, urlopen, sleep, uniform) -> None:
        urlopen.side_effect = [_http_error(429, retry_after="7"), _Response(b'{"ok": true}')]

        request_json("GET", "https://example.test", None, ModelLoadError, "failed")

        sleep.assert_called_once_with(7.0)
        uniform.assert_not_called()

    @mock.patch("subtitler.hosted_http.time.time", return_value=1_700_000_000.0)
    @mock.patch("subtitler.hosted_http.random.uniform")
    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen")
    def test_retry_after_http_date_is_supported(self, urlopen, sleep, uniform, _now) -> None:
        retry_at = datetime.fromtimestamp(1_700_000_009, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        urlopen.side_effect = [_http_error(503, retry_after=retry_at), _Response(b'{"ok": true}')]

        request_json("GET", "https://example.test", None, ModelLoadError, "failed")

        sleep.assert_called_once_with(9.0)
        uniform.assert_not_called()

    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen", side_effect=lambda *_a, **_k: (_ for _ in ()).throw(_http_error(400)))
    def test_nonretryable_http_fails_immediately(self, urlopen, sleep) -> None:
        with self.assertRaisesRegex(ModelLoadError, "HTTP 400"):
            request_json("GET", "https://example.test", None, ModelLoadError, "failed")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    @mock.patch("subtitler.hosted_http.random.uniform", return_value=0.0)
    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen", side_effect=TimeoutError("timed out"))
    def test_timeout_retries_then_uses_exhaustion_error_type(self, urlopen, sleep, _uniform) -> None:
        with self.assertRaises(DeadTranscriptionRequest):
            _request_json(
                "GET",
                "https://example.test",
                None,
                TranscriptionError,
                "failed",
                dead_request_error_type=DeadTranscriptionRequest,
            )
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen", return_value=_Response(b"not json"))
    def test_malformed_response_is_not_retried(self, urlopen, sleep) -> None:
        with self.assertRaisesRegex(TranscriptionError, "malformed JSON"):
            _request_json("GET", "https://example.test", None, TranscriptionError, "failed")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen")
    def test_credentials_are_redacted_from_provider_error(self, urlopen, sleep) -> None:
        key = "super-secret-key"
        urlopen.side_effect = _http_error(401, f'credential {key} and Bearer {key}')
        with self.assertRaises(ModelLoadError) as raised:
            request_json(
                "GET",
                "https://example.test",
                None,
                ModelLoadError,
                f"request for ?key={key} failed",
                headers={"x-goog-api-key": key},
            )
        self.assertNotIn(key, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))
        sleep.assert_not_called()

    @mock.patch("subtitler.hosted_http.random.uniform", return_value=0.0)
    @mock.patch("subtitler.hosted_http.time.sleep")
    @mock.patch("subtitler.hosted_http.urllib.request.urlopen")
    def test_dead_request_is_exposed_after_primary_retry_exhaustion(self, urlopen, sleep, _uniform) -> None:
        urlopen.side_effect = [_http_error(503), _http_error(503), _http_error(503)]
        chunk = AudioChunk(index=1, start=0.0, end=1.0, samples=[])

        class Primary:
            provider = "gemini"
            model = "primary"

            def transcribe(self, value: AudioChunk) -> TranscriptChunk:
                _request_json(
                    "GET",
                    "https://example.test",
                    None,
                    TranscriptionError,
                    "failed",
                    dead_request_error_type=DeadTranscriptionRequest,
                )
                raise AssertionError("unreachable")

        fallback = mock.Mock(provider="openai", model="fallback")
        with self.assertRaises(DeadTranscriptionRequest):
            FallbackTranscriber(Primary(), fallback).transcribe(chunk)
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        fallback.transcribe.assert_not_called()

    def test_nonretryable_primary_failure_does_not_invoke_fallback(self) -> None:
        chunk = AudioChunk(index=1, start=0.0, end=1.0, samples=[])
        primary = mock.Mock(provider="gemini", model="primary")
        primary.transcribe.side_effect = TranscriptionError("HTTP 400")
        fallback = mock.Mock(provider="openai", model="fallback")

        with self.assertRaises(TranscriptionError):
            FallbackTranscriber(primary, fallback).transcribe(chunk)

        fallback.transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
