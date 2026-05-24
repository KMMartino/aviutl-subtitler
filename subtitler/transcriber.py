"""Gemma transcription backends."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .audio import write_wav_segment
from .errors import ModelLoadError, OutOfMemoryError, TranscriptionError
from .glossary import GlossaryEntry, format_glossary
from .models import AudioChunk, TranscriptChunk
from .vad import split_chunk_with_tighter_vad


PROMPT = (
    "この音声の日本語の発話を、聞こえた順番どおりに一字一句そのまま文字起こししてください。"
    "翻訳、要約、補足、タイムスタンプ、記号トークンは出力しないでください。"
    "文字起こし本文だけを出力してください。"
)

TRANSCRIPTION_STOP = ["<|im_end|>", "<end_of_turn>", "<|end|>", "<|eot_id|>"]


def build_transcription_prompt(glossary: list[GlossaryEntry] | None = None) -> str:
    if not glossary:
        return PROMPT
    hints = format_glossary(glossary)
    return (
        "この音声の日本語の発話を、聞こえた順番どおりに一字一句そのまま文字起こししてください。\n"
        "翻訳、要約、補足、タイムスタンプ、記号トークンは出力しないでください。\n"
        "文字起こし本文だけを出力してください。\n"
        "以下の語彙ヒントは、音声として聞こえる場合だけ使ってください:\n"
        f"{hints}"
    )


@contextlib.contextmanager
def _suppress_native_output(enabled: bool):
    if not enabled:
        yield
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull)


def clean_transcript(text: str) -> str:
    text = text.strip()
    for stop in TRANSCRIPTION_STOP:
        text = text.replace(stop, "")
    text = re.sub(r"(はい、?承知(?:いた)?しました。?)", "", text).strip()
    text = re.sub(r"^(承知(?:いた)?しました。?)", "", text).strip()
    text = re.sub(r"^```(?:text)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    text = re.sub(r"^\s*\[[0-9:.]+\s*[-–]\s*[0-9:.]+\]\s*", "", text)
    prefixes = ["Transcription:", "Transcript:", "Output:", "Text:"]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()
    return text.strip().strip('"')


def _is_oom(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(term in message for term in ["out of memory", "oom", "vk_error_out_of_device_memory"])


def _looks_like_ignored_audio(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    bad_exact = {
        "the",
        "thecatsatonthemat",
        "thecatsatonthemat.",
    }
    bad_contains = {
        "audiosegmentprovideduser",
        "whatisthecapitalofaustralia",
        "thecapitalofaustraliaiscanberra",
    }
    return normalized in bad_exact or any(pattern in normalized for pattern in bad_contains)


class GemmaTranscriber:
    def __init__(
        self,
        model_path: Path,
        mmproj: Path | None,
        n_gpu_layers: int,
        ctx_size: int,
        threads: int | None,
        batch_size: int | None,
        temp_dir: Path,
        sample_rate: int,
        verbose: bool = False,
    ) -> None:
        if not model_path.exists():
            raise ModelLoadError(f"Gemma GGUF model not found: {model_path}")
        if mmproj is not None and not mmproj.exists():
            raise ModelLoadError(f"Multimodal projector file not found: {mmproj}")
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import Llava15ChatHandler
        except ImportError as exc:
            raise ModelLoadError("llama-cpp-python is not installed") from exc

        chat_handler = None
        if mmproj is not None:
            chat_handler = Llava15ChatHandler(clip_model_path=str(mmproj), verbose=verbose)

        kwargs = {
            "model_path": str(model_path),
            "n_gpu_layers": n_gpu_layers,
            "n_ctx": ctx_size,
            "verbose": verbose,
        }
        if threads:
            kwargs["n_threads"] = threads
        if batch_size:
            kwargs["n_batch"] = batch_size
        if chat_handler is not None:
            kwargs["chat_handler"] = chat_handler
        try:
            with _suppress_native_output(not verbose):
                self.model = Llama(**kwargs)
        except Exception as exc:
            if _is_oom(exc):
                raise OutOfMemoryError(
                    "Loading the GGUF model appears to have exhausted GPU memory. "
                    "Use a smaller quantization, lower --ctx-size, or fewer --n-gpu-layers."
                ) from exc
            raise ModelLoadError(f"Could not load Gemma model: {exc}") from exc
        self.temp_dir = temp_dir
        self.sample_rate = sample_rate
        self.verbose = verbose

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        wav_path = chunk.wav_path or self.temp_dir / f"transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, self.sample_rate, wav_path)

        try:
            # llama-cpp-python multimodal APIs are version/model dependent. This
            # uses the chat format accepted by recent llama.cpp bindings; if the
            # installed stack does not support audio content, the error is surfaced.
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "audio", "audio": str(wav_path)},
                    ],
                },
            ]
            with _suppress_native_output(not self.verbose):
                response = self.model.create_chat_completion(
                    messages=messages,
                    temperature=0.0,
                    max_tokens=512,
                    stop=TRANSCRIPTION_STOP,
                )
            text = response["choices"][0]["message"]["content"]
        except Exception as exc:
            if _is_oom(exc):
                raise OutOfMemoryError(
                    "Transcription appears to have exhausted GPU memory. "
                    "Use a smaller model or reduce --ctx-size/--n-gpu-layers."
                ) from exc
            raise TranscriptionError(f"Gemma transcription failed for chunk {chunk.index}: {exc}") from exc
        cleaned = clean_transcript(str(text))
        if _looks_like_ignored_audio(cleaned):
            raise TranscriptionError(
                f"Gemma returned known template/placeholder text ({cleaned!r}), "
                "which indicates the audio was not consumed by llama-cpp-python. "
                "This model path needs a working audio-capable multimodal handler, "
                "native llama.cpp audio routing, or a different transcription implementation."
            )
        return TranscriptChunk(chunk=chunk, text=cleaned)

    def suggest_split(self, text: str, max_chars: int) -> tuple[str, str] | None:
        prompt = (
            "Identify the most natural grammatical break point in this sentence "
            f"to split it into two lines. Both lines must be under {max_chars} "
            "characters. Output only the two resulting strings.\n\n"
            f"{text}"
        )
        try:
            with _suppress_native_output(not self.verbose):
                response = self.model.create_completion(prompt=prompt, temperature=0.0, max_tokens=256)
            raw = str(response["choices"][0]["text"]).strip()
        except Exception:
            return None
        lines = [line.strip().strip('"') for line in raw.splitlines() if line.strip()]
        if len(lines) != 2:
            return None
        left, right = lines
        if len(left) > max_chars or len(right) > max_chars:
            return None
        if re.sub(r"\s+", "", left + right) != re.sub(r"\s+", "", text):
            return None
        return left, right


class NativeGemmaTranscriber:
    """Transcribe audio through llama.cpp's native multimodal CLI."""

    def __init__(
        self,
        model_path: Path,
        mmproj: Path,
        n_gpu_layers: int,
        ctx_size: int,
        temp_dir: Path,
        cli_path: Path | None = None,
    ) -> None:
        if not model_path.exists():
            raise ModelLoadError(f"Gemma GGUF model not found: {model_path}")
        if not mmproj.exists():
            raise ModelLoadError(f"Gemma projector file not found: {mmproj}")
        self.cli_path = self._resolve_cli(cli_path)
        self.model_path = model_path
        self.mmproj = mmproj
        self.n_gpu_layers = n_gpu_layers
        self.ctx_size = ctx_size
        self.temp_dir = temp_dir

    @staticmethod
    def _resolve_cli(cli_path: Path | None) -> Path:
        if cli_path is not None:
            if not cli_path.exists():
                raise ModelLoadError(f"llama-mtmd-cli not found: {cli_path}")
            return cli_path
        found = shutil.which("llama-mtmd-cli") or shutil.which("llama-mtmd-cli.exe")
        if found:
            return Path(found)
        common = Path(r"C:\tools\llama-vulkan\llama-mtmd-cli.exe")
        if common.exists():
            return common
        raise ModelLoadError(
            "llama-mtmd-cli was not found on PATH or at C:\\tools\\llama-vulkan\\llama-mtmd-cli.exe"
        )

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        wav_path = chunk.wav_path or self.temp_dir / f"native_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)

        gpu_layers = "all" if self.n_gpu_layers < 0 else str(self.n_gpu_layers)
        cmd = [
            str(self.cli_path),
            "-m",
            str(self.model_path),
            "--mmproj",
            str(self.mmproj),
            "--audio",
            str(wav_path),
            "-p",
            PROMPT,
            "-ngl",
            gpu_layers,
            "-c",
            str(self.ctx_size),
            "--jinja",
            "--no-warmup",
            "--log-verbosity",
            "1",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise TranscriptionError(f"Could not run llama-mtmd-cli: {exc}") from exc

        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0:
            raise TranscriptionError(
                f"llama-mtmd-cli failed for chunk {chunk.index} with exit code "
                f"{result.returncode}:\n{output}"
            )
        cleaned = clean_transcript(_extract_native_transcript(output))
        if not cleaned:
            raise TranscriptionError(f"llama-mtmd-cli returned an empty transcript for chunk {chunk.index}")
        if _looks_like_ignored_audio(cleaned):
            raise TranscriptionError(
                f"llama-mtmd-cli returned known template/placeholder text ({cleaned!r})"
            )
        return TranscriptChunk(chunk=chunk, text=cleaned)

    def suggest_split(self, text: str, max_chars: int) -> tuple[str, str] | None:
        return None


class ServerGemmaTranscriber:
    """Transcribe audio through a managed llama.cpp HTTP server."""

    def __init__(
        self,
        model_path: Path,
        mmproj: Path,
        n_gpu_layers: int,
        ctx_size: int,
        temp_dir: Path,
        server_path: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8081,
        glossary: list[GlossaryEntry] | None = None,
        max_transcription_split_depth: int = 2,
    ) -> None:
        if not model_path.exists():
            raise ModelLoadError(f"Gemma GGUF model not found: {model_path}")
        if not mmproj.exists():
            raise ModelLoadError(f"Gemma projector file not found: {mmproj}")
        self.server_path = self._resolve_server(server_path)
        self.model_path = model_path
        self.mmproj = mmproj
        self.n_gpu_layers = n_gpu_layers
        self.ctx_size = ctx_size
        self.temp_dir = temp_dir
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.prompt = build_transcription_prompt(glossary)
        self.max_transcription_split_depth = max(0, max_transcription_split_depth)
        self.process: subprocess.Popen[str] | None = None
        self._owned_process = False
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
            return

        gpu_layers = "all" if self.n_gpu_layers < 0 else str(self.n_gpu_layers)
        cmd = [
            str(self.server_path),
            "-m",
            str(self.model_path),
            "--mmproj",
            str(self.mmproj),
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
        ]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            raise ModelLoadError(f"Could not start llama-server: {exc}") from exc
        self._owned_process = True

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ModelLoadError(f"llama-server exited early with code {self.process.returncode}")
            if self._health_ok():
                return
            time.sleep(1)
        self.close()
        raise ModelLoadError("llama-server did not become healthy within 180 seconds")

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def transcribe(self, chunk: AudioChunk) -> TranscriptChunk:
        payload = self.prepare_payload(chunk)
        text = self.transcribe_payload(chunk, payload)
        return TranscriptChunk(chunk=chunk, text=text)

    def prepare_payload(self, chunk: AudioChunk) -> dict:
        wav_path = chunk.wav_path or self.temp_dir / f"server_transcribe_{chunk.index:05d}.wav"
        if chunk.wav_path is None:
            write_wav_segment(chunk.samples, 16000, wav_path)

        audio_data = base64.b64encode(wav_path.read_bytes()).decode("ascii")
        return {
            "temperature": 0.0,
            "max_tokens": 512,
            "stop": TRANSCRIPTION_STOP,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt},
                        {"type": "input_audio", "input_audio": {"data": audio_data, "format": "wav"}},
                    ],
                }
            ],
        }

    def transcribe_payload(self, chunk: AudioChunk, payload: dict, depth: int = 0) -> str:
        cleaned = self._transcribe_payload_once(chunk, payload)
        if _is_suspect_transcript(cleaned, chunk) and depth < self.max_transcription_split_depth:
            subchunks = split_chunk_with_tighter_vad(
                chunk,
                sample_rate=16000,
                temp_dir=self.temp_dir,
                keep_temp=True,
            )
            if len(subchunks) >= 2:
                print(
                    f"Warning: suspect transcription for chunk {chunk.index} "
                    f"[{chunk.start:.2f}-{chunk.end:.2f}s]; retrying as {len(subchunks)} subchunks.",
                    flush=True,
                )
                parts = []
                for subchunk in subchunks:
                    subpayload = self.prepare_payload(subchunk)
                    parts.append(self.transcribe_payload(subchunk, subpayload, depth + 1))
                merged = _merge_transcript_parts(parts)
                if merged:
                    return merged
        return cleaned

    def _transcribe_payload_once(self, chunk: AudioChunk, payload: dict) -> str:
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise TranscriptionError(f"llama-server transcription failed for chunk {chunk.index}: {exc}") from exc

        cleaned = clean_transcript(str(text))
        if not cleaned:
            raise TranscriptionError(f"llama-server returned an empty transcript for chunk {chunk.index}")
        if _looks_like_ignored_audio(cleaned):
            raise TranscriptionError(f"llama-server returned known template/placeholder text ({cleaned!r})")
        return cleaned

    def suggest_split(self, text: str, max_chars: int) -> tuple[str, str] | None:
        return None

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


def _extract_native_transcript(output: str) -> str:
    lines = [line.strip() for line in output.splitlines()]
    lines = [line for line in lines if line and not re.match(r"^\d+\.\d+\.\d+\.\d+\s+[A-Z]\s+", line)]
    lines = [line for line in lines if "llama_" not in line and "common_" not in line and "mtmd_" not in line]
    return "\n".join(lines).strip()


def _merge_transcript_parts(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if part.strip()]
    return "".join(cleaned)


def _is_suspect_transcript(text: str, chunk: AudioChunk) -> bool:
    normalized = re.sub(r"\s+", "", text)
    duration = max(0.0, chunk.end - chunk.start)
    if not normalized:
        return True
    if duration >= 12 and len(normalized) < duration * 3.0:
        return True
    if duration >= 20 and len(normalized) < duration * 4.0:
        return True
    if _has_repeated_text_loop(normalized):
        return True
    if "承知" in text:
        return True
    if re.match(r"^(R|SR|SSR|DNA|NA|Pro|Series|X)[がをはにの、 ]", text.strip()):
        return True
    if re.search(r"(ま、?\s*PS|PlayStation\s*5\s*Pro\s*に|RDNA|RNA|RA)$", text.strip()):
        return duration >= 8
    return False


def _has_repeated_text_loop(text: str) -> bool:
    if len(text) < 60:
        return False
    for size in range(12, min(80, len(text) // 2) + 1):
        counts: dict[str, int] = {}
        for start in range(0, len(text) - size + 1, max(1, size // 3)):
            piece = text[start : start + size]
            counts[piece] = counts.get(piece, 0) + 1
            if counts[piece] >= 3:
                return True
    return False
