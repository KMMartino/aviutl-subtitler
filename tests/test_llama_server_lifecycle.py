import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from subtitler.errors import ModelLoadError
from subtitler.llama_server import LlamaServerProcess


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class LlamaServerLifecycleTests(unittest.TestCase):
    def make_server(self, model: Path) -> LlamaServerProcess:
        server = LlamaServerProcess.__new__(LlamaServerProcess)
        server.model_path = model.resolve()
        server.host = "127.0.0.1"
        server.port = 18081
        server.base_url = "http://127.0.0.1:18081"
        return server

    def urlopen_for_model(
        self,
        identifier: str,
        model_path: str,
        *,
        size: int = 0,
        owned_by: str = "llamacpp",
    ):
        def open_url(url, timeout):
            self.assertEqual(timeout, 2)
            if str(url).endswith("/health"):
                return _Response(b"{}")
            if str(url).endswith("/props"):
                return _Response(json.dumps({"model_path": model_path, "build_info": "b9264"}).encode())
            return _Response(
                json.dumps(
                    {
                        "object": "list",
                        "data": [{"id": identifier, "owned_by": owned_by, "meta": {"size": size}}],
                    }
                ).encode()
            )

        return open_url

    def test_existing_server_is_reused_only_for_requested_model(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "transcription.gguf"
            model.write_bytes(b"model")
            server = self.make_server(model)
            with patch(
                "subtitler.llama_server.urllib.request.urlopen",
                side_effect=self.urlopen_for_model(model.name, str(model), size=model.stat().st_size - 1),
            ):
                server._require_compatible_identity(existing=True)

    def test_existing_server_with_wrong_model_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "transcription.gguf"
            model.write_bytes(b"model")
            server = self.make_server(model)
            with patch(
                "subtitler.llama_server.urllib.request.urlopen",
                side_effect=self.urlopen_for_model("cleanup.gguf", str(model), size=5),
            ):
                with self.assertRaisesRegex(ModelLoadError, "different or incompatible model"):
                    server._require_compatible_identity(existing=True)

    def test_tensor_payload_size_can_differ_from_gguf_file_size(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "transcription.gguf"
            model.write_bytes(b"expected")
            server = self.make_server(model)
            with patch(
                "subtitler.llama_server.urllib.request.urlopen",
                side_effect=self.urlopen_for_model(model.name, str(model), size=model.stat().st_size + 1),
            ):
                server._require_compatible_identity(existing=True)

    def test_same_named_model_at_different_path_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "transcription.gguf"
            model.write_bytes(b"expected")
            server = self.make_server(model)
            other_path = model.parent / "other" / model.name
            with patch(
                "subtitler.llama_server.urllib.request.urlopen",
                side_effect=self.urlopen_for_model(model.name, str(other_path), size=8),
            ):
                with self.assertRaisesRegex(ModelLoadError, "incompatible model path"):
                    server._require_compatible_identity(existing=True)

    def test_foreign_model_list_shape_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "transcription.gguf"
            model.write_bytes(b"expected")
            server = self.make_server(model)
            with patch(
                "subtitler.llama_server.urllib.request.urlopen",
                side_effect=self.urlopen_for_model(model.name, str(model), owned_by="foreign-service"),
            ):
                with self.assertRaisesRegex(ModelLoadError, "different or incompatible model"):
                    server._require_compatible_identity(existing=True)

    def test_foreign_service_on_port_fails_clearly(self):
        server = self.make_server(Path("C:/models/transcription.gguf"))
        foreign = _Response(b"not found")
        foreign.status = 404
        with patch("subtitler.llama_server.urllib.request.urlopen", return_value=foreign):
            with self.assertRaisesRegex(ModelLoadError, "does not expose a healthy llama.cpp endpoint"):
                server._require_compatible_identity(existing=True)

    def test_spawn_failure_closes_log_and_normalizes_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "llama-server.exe"
            executable.touch()
            model = root / "model.gguf"
            model.touch()
            log = root / "server.log"
            with (
                patch.object(LlamaServerProcess, "_port_is_open", return_value=False),
                patch("subtitler.llama_server.subprocess.Popen", side_effect=OSError("fixture failure")),
            ):
                with self.assertRaisesRegex(ModelLoadError, "Could not start fixture server: fixture failure"):
                    LlamaServerProcess(
                        model_path=model,
                        server_path=executable,
                        host="127.0.0.1",
                        port=18081,
                        ctx_size=1024,
                        n_gpu_layers=0,
                        log_path=log,
                        label="fixture server",
                    )
            with log.open("a", encoding="utf-8") as handle:
                handle.write("closed")

    def test_early_exit_closes_log_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "llama-server.exe"
            executable.touch()
            model = root / "model.gguf"
            model.touch()
            log = root / "server.log"
            process = Mock()
            process.poll.return_value = 7
            process.returncode = 7
            with (
                patch.object(LlamaServerProcess, "_port_is_open", return_value=False),
                patch("subtitler.llama_server.subprocess.Popen", return_value=process),
            ):
                with self.assertRaisesRegex(ModelLoadError, "exited early with code 7"):
                    LlamaServerProcess(
                        model_path=model,
                        server_path=executable,
                        host="127.0.0.1",
                        port=18081,
                        ctx_size=1024,
                        n_gpu_layers=0,
                        log_path=log,
                        label="fixture server",
                        timeout_seconds=0.1,
                        poll_interval=0,
                    )
            with log.open("a", encoding="utf-8") as handle:
                handle.write("closed")

    def test_startup_timeout_terminates_owned_process_and_closes_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "llama-server.exe"
            executable.touch()
            model = root / "model.gguf"
            model.touch()
            log = root / "server.log"
            process = Mock()
            process.poll.return_value = None
            process.wait.return_value = 0
            with (
                patch.object(LlamaServerProcess, "_port_is_open", return_value=False),
                patch.object(LlamaServerProcess, "_health_ok", return_value=False),
                patch("subtitler.llama_server.subprocess.Popen", return_value=process),
            ):
                with self.assertRaisesRegex(ModelLoadError, "did not become healthy within 0.01 seconds"):
                    LlamaServerProcess(
                        model_path=model,
                        server_path=executable,
                        host="127.0.0.1",
                        port=18081,
                        ctx_size=1024,
                        n_gpu_layers=0,
                        log_path=log,
                        label="fixture server",
                        timeout_seconds=0.01,
                        poll_interval=0,
                    )
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=10)
            with log.open("a", encoding="utf-8") as handle:
                handle.write("closed")

    def test_close_escalates_after_graceful_timeout_and_closes_log(self):
        server = self.make_server(Path("C:/models/model.gguf"))
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 10), 0]
        log = io.StringIO()
        server.process = process
        server.owned_process = True
        server._log_handle = log
        server.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertTrue(log.closed)


if __name__ == "__main__":
    unittest.main()
