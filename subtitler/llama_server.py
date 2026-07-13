"""Shared lifecycle management for local llama.cpp servers."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TextIO

from .errors import ModelLoadError


class LlamaServerProcess:
    """Resolve, identify, launch, and stop one llama-server instance."""

    def __init__(
        self,
        *,
        model_path: Path,
        server_path: Path | None,
        host: str,
        port: int,
        ctx_size: int,
        n_gpu_layers: int,
        extra_args: list[str] | None = None,
        log_path: Path | None = None,
        label: str = "llama-server",
        ready_message: str | None = None,
        wait_message: str | None = None,
        timeout_seconds: float = 180,
        poll_interval: float = 1,
    ) -> None:
        self.model_path = model_path.resolve()
        self.server_path = self.resolve_server(server_path)
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.ctx_size = ctx_size
        self.n_gpu_layers = n_gpu_layers
        self.extra_args = extra_args or []
        self.log_path = log_path
        self.label = label
        self.ready_message = ready_message
        self.wait_message = wait_message
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self.process: subprocess.Popen[str] | None = None
        self.owned_process = False
        self._log_handle: TextIO | None = None
        self.ensure_ready()

    @staticmethod
    def resolve_server(server_path: Path | None) -> Path:
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

    def ensure_ready(self) -> None:
        if self._port_is_open():
            self._require_compatible_identity(existing=True)
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
            *self.extra_args,
        ]
        stdout: int | TextIO = subprocess.DEVNULL
        stderr: int = subprocess.DEVNULL
        try:
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = self.log_path.open("w", encoding="utf-8")
                self._log_handle.write(" ".join(cmd) + "\n\n")
                self._log_handle.flush()
                stdout = self._log_handle
                stderr = subprocess.STDOUT
                print(f"{self.label} log: {self.log_path}", flush=True)
            self.process = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, text=True)
            self.owned_process = True
        except (OSError, ValueError) as exc:
            self._close_log()
            raise ModelLoadError(f"Could not start {self.label}: {exc}") from exc

        deadline = time.monotonic() + self.timeout_seconds
        next_notice = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    detail = f" See log: {self.log_path}" if self.log_path is not None else ""
                    raise ModelLoadError(
                        f"{self.label} exited early with code {self.process.returncode}."
                        f"{detail}{tail_log(self.log_path)}"
                    )
                if self._health_ok():
                    self._require_compatible_identity(existing=False)
                    if self.ready_message:
                        print(self.ready_message, flush=True)
                    return
                if self.wait_message and time.monotonic() >= next_notice:
                    print(self.wait_message, flush=True)
                    next_notice = time.monotonic() + 10
                time.sleep(self.poll_interval)
            detail = f" See log: {self.log_path}" if self.log_path is not None else ""
            raise ModelLoadError(
                f"{self.label} did not become healthy within {self.timeout_seconds:g} seconds.{detail}"
            )
        except Exception:
            self.close()
            raise

    def _port_is_open(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                return True
        except OSError:
            return False

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _require_compatible_identity(self, *, existing: bool) -> None:
        context = "already-running service" if existing else "started llama-server"
        if not self._health_ok():
            raise ModelLoadError(
                f"Port {self.port} is occupied, but the {context} does not expose a healthy llama.cpp endpoint."
            )
        try:
            with urllib.request.urlopen(f"{self.base_url}/v1/models", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            entries = payload.get("data") if isinstance(payload, dict) else None
            model_entries = [entry for entry in entries or [] if isinstance(entry, dict)]
            with urllib.request.urlopen(f"{self.base_url}/props", timeout=2) as response:
                props = json.loads(response.read().decode("utf-8"))
            served_path = props.get("model_path") if isinstance(props, dict) else None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ModelLoadError(
                f"Port {self.port} is occupied, but the {context} did not provide valid llama.cpp model metadata."
            ) from exc
        if not model_entries or not any(self._model_entry_matches(entry) for entry in model_entries):
            shown = ", ".join(repr(entry.get("id")) for entry in model_entries) or "none"
            raise ModelLoadError(
                f"Port {self.port} is serving a different or incompatible model ({shown}); "
                f"expected llama.cpp model {self.model_path.name!r}. "
                "Stop that service or choose another port."
            )
        if not isinstance(served_path, str) or self._canonical_path(served_path) != self._canonical_path(self.model_path):
            raise ModelLoadError(
                f"Port {self.port} is serving a different or incompatible model path "
                f"({served_path!r}); expected {str(self.model_path)!r}. Stop that service or choose another port."
            )

    def _model_entry_matches(self, entry: dict[object, object]) -> bool:
        value = entry.get("id")
        if not isinstance(value, str):
            return False
        normalized = value.replace("\\", "/").rstrip("/").casefold()
        expected = self.model_path.as_posix().casefold()
        identifier_matches = normalized == expected or normalized.rsplit("/", 1)[-1] == self.model_path.name.casefold()
        return identifier_matches and entry.get("owned_by") == "llamacpp"

    @staticmethod
    def _canonical_path(value: str | Path) -> str:
        return str(Path(value).resolve()).replace("\\", "/").rstrip("/").casefold()

    def close(self) -> None:
        try:
            if self.process is not None and self.owned_process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        finally:
            self._close_log()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def tail_log(path: Path | None, max_chars: int = 2000) -> str:
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = text[-max_chars:].strip()
    return f"\nLast llama-server log lines:\n{tail}" if tail else ""
