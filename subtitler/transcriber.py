"""Gemma transcription backends."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TextIO

from .audio import write_wav_segment
from .errors import ModelLoadError, TranscriptionError
from .glossary import GlossaryEntry, format_glossary
from .models import AudioChunk, TranscriptChunk
from .vad import split_chunk_with_tighter_vad


UNTRANSCRIBABLE_AUDIO_TOKEN = "__SUBTITLER_UNTRANSCRIBABLE_AUDIO__"

PROMPT = (
    "この音声の日本語の発話を、聞こえた順番どおりに一字一句そのまま文字起こししてください。"
    "翻訳、要約、補足、タイムスタンプ、記号トークンは出力しないでください。"
    f"音声が判別不能、無音、ノイズのみ、または日本語の発話として文字起こしできない場合は、必ず {UNTRANSCRIBABLE_AUDIO_TOKEN} だけを出力してください。"
    "文字起こし本文だけを出力してください。"
)

TRANSCRIPTION_STOP = ["<|im_end|>", "<end_of_turn>", "<|end|>", "<|eot_id|>"]
EMPTY_TRANSCRIPT_ATTEMPTS = 2


def build_transcription_prompt(glossary: list[GlossaryEntry] | None = None) -> str:
    if not glossary:
        return PROMPT
    hints = format_glossary(glossary)
    return (
        "この音声の日本語の発話を、聞こえた順番どおりに一字一句そのまま文字起こししてください。\n"
        "翻訳、要約、補足、タイムスタンプ、記号トークンは出力しないでください。\n"
        f"音声が判別不能、無音、ノイズのみ、または日本語の発話として文字起こしできない場合は、必ず {UNTRANSCRIBABLE_AUDIO_TOKEN} だけを出力してください。\n"
        "文字起こし本文だけを出力してください。\n"
        "以下の語彙ヒントは、音声として聞こえる場合だけ使ってください:\n"
        f"{hints}"
    )


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


class ServerGemmaTranscriber:
    """Transcribe audio through a managed llama.cpp HTTP server."""

    def __init__(
        self,
        model_path: Path,
        mmproj: Path | None,
        n_gpu_layers: int,
        ctx_size: int,
        temp_dir: Path,
        server_path: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8081,
        glossary: list[GlossaryEntry] | None = None,
        max_transcription_split_depth: int = 2,
        spec_draft_model: Path | None = None,
        spec_draft_n_max: int = 3,
        log_path: Path | None = None,
    ) -> None:
        if not model_path.exists():
            raise ModelLoadError(f"Gemma GGUF model not found: {model_path}")
        if mmproj is not None and not mmproj.exists():
            raise ModelLoadError(f"Gemma projector file not found: {mmproj}")
        if spec_draft_model is not None and not spec_draft_model.exists():
            raise ModelLoadError(f"Speculative draft/MTP model not found: {spec_draft_model}")
        if spec_draft_model is not None and spec_draft_model.suffix.lower() != ".gguf":
            raise ModelLoadError(
                "Speculative draft/MTP model must be a GGUF file for llama.cpp. "
                f"Got: {spec_draft_model}"
            )
        self.server_path = self._resolve_server(server_path)
        self.model_path = model_path
        self.mmproj = mmproj
        self.spec_draft_model = spec_draft_model
        self.spec_draft_n_max = max(1, spec_draft_n_max)
        self.log_path = log_path
        self._log_handle: TextIO | None = None
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
        if self.mmproj is not None:
            cmd.extend(["--mmproj", str(self.mmproj)])
        if self.spec_draft_model is not None:
            cmd.extend(
                [
                    "--spec-draft-model",
                    str(self.spec_draft_model),
                    "--spec-type",
                    "draft-mtp",
                    "--spec-draft-n-max",
                    str(self.spec_draft_n_max),
                ]
            )
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("w", encoding="utf-8")
            self._log_handle.write(" ".join(cmd) + "\n\n")
            self._log_handle.flush()
            stdout = self._log_handle
            stderr = subprocess.STDOUT
            print(f"Transcription llama-server log: {self.log_path}", flush=True)
        else:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
        except OSError as exc:
            raise ModelLoadError(f"Could not start llama-server: {exc}") from exc
        self._owned_process = True

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                detail = f" See log: {self.log_path}" if self.log_path is not None else ""
                tail = _tail_log(self.log_path) if self.log_path is not None else ""
                raise ModelLoadError(
                    f"llama-server exited early with code {self.process.returncode}.{detail}{tail}"
                )
            if self._health_ok():
                return
            time.sleep(1)
        self.close()
        detail = f" See log: {self.log_path}" if self.log_path is not None else ""
        raise ModelLoadError(f"llama-server did not become healthy within 180 seconds.{detail}")

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
        cleaned = self._transcribe_payload_with_empty_retry(chunk, payload)
        if not cleaned:
            return ""
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

    def _transcribe_payload_with_empty_retry(self, chunk: AudioChunk, payload: dict) -> str:
        for attempt in range(EMPTY_TRANSCRIPT_ATTEMPTS):
            cleaned = self._transcribe_payload_once(chunk, payload)
            if cleaned:
                return cleaned
            if attempt < EMPTY_TRANSCRIPT_ATTEMPTS - 1:
                print(
                    f"Warning: llama-server returned an empty transcript for chunk {chunk.index}; "
                    f"retrying attempt {attempt + 2}/{EMPTY_TRANSCRIPT_ATTEMPTS}.",
                    flush=True,
                )
                time.sleep(1)
        print(
            f"Warning: llama-server returned an empty transcript for chunk {chunk.index} "
            f"after {EMPTY_TRANSCRIPT_ATTEMPTS} attempts; skipping this chunk.",
            flush=True,
        )
        return ""

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
        if _looks_like_ignored_audio(cleaned):
            raise TranscriptionError(f"llama-server returned known template/placeholder text ({cleaned!r})")
        return cleaned

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
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def _tail_log(path: Path, max_chars: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = text[-max_chars:].strip()
    return f"\nLast llama-server log lines:\n{tail}" if tail else ""

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
