"""Gemma transcription backends."""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from pathlib import Path

from .audio import write_wav_segment
from .errors import ModelLoadError, TranscriptionError
from .glossary import GlossaryEntry, format_glossary
from .llama_server import LlamaServerProcess
from .models import AudioChunk, TranscriptChunk
from .vad import VadSession, split_chunk_with_tighter_vad


UNTRANSCRIBABLE_AUDIO_TOKEN = "__SUBTITLER_UNTRANSCRIBABLE_AUDIO__"

PROMPT = (
    "この音声の日本語の発話を、聞こえた順番どおりに一字一句そのまま文字起こししてください。"
    "翻訳、要約、補足、タイムスタンプ、記号トークンは出力しないでください。"
    f"音声が判別不能、無音、ノイズのみ、または日本語の発話として文字起こしできない場合は、必ず {UNTRANSCRIBABLE_AUDIO_TOKEN} だけを出力してください。"
    "文字起こし本文だけを出力してください。"
)

TRANSCRIPTION_STOP = ["<|im_end|>", "<end_of_turn>", "<|end|>", "<|eot_id|>"]
def build_transcription_prompt(
    glossary: list[GlossaryEntry] | None = None,
    previous_transcript: str | None = None,
) -> str:
    if not glossary:
        prompt = PROMPT
    else:
        hints = format_glossary(glossary)
        prompt = (
        "この音声の日本語の発話を、聞こえた順番どおりに一字一句そのまま文字起こししてください。\n"
        "翻訳、要約、補足、タイムスタンプ、記号トークンは出力しないでください。\n"
        f"音声が判別不能、無音、ノイズのみ、または日本語の発話として文字起こしできない場合は、必ず {UNTRANSCRIBABLE_AUDIO_TOKEN} だけを出力してください。\n"
        "文字起こし本文だけを出力してください。\n"
        "以下の語彙ヒントは、音声として聞こえる場合だけ使ってください:\n"
        f"{hints}"
        )
    if not previous_transcript:
        return prompt
    safe_context = previous_transcript.replace("<previous_transcript>", "＜previous_transcript＞").replace(
        "</previous_transcript>", "＜/previous_transcript＞"
    )
    return (
        f"{prompt}\n\n"
        "直前の音声区間の文字起こしを文脈として示します:\n"
        "<previous_transcript>\n"
        f"{safe_context}\n"
        "</previous_transcript>\n\n"
        "これは文脈参照専用です。添付された現在の音声区間だけを文字起こししてください。\n"
        "現在の音声で実際に聞こえない内容を補完したり、直前の文字起こしを繰り返したりしないでください。"
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
        vad_session: VadSession | None = None,
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
        self.model_path = model_path
        self.mmproj = mmproj
        self.spec_draft_model = spec_draft_model
        self.spec_draft_n_max = max(1, spec_draft_n_max)
        self.n_gpu_layers = n_gpu_layers
        self.ctx_size = ctx_size
        self.temp_dir = temp_dir
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.prompt = build_transcription_prompt(glossary)
        self.glossary = glossary
        self.max_transcription_split_depth = max(0, max_transcription_split_depth)
        self.vad_session = vad_session
        extra_args: list[str] = []
        if self.mmproj is not None:
            extra_args.extend(["--mmproj", str(self.mmproj)])
        if self.spec_draft_model is not None:
            extra_args.extend(
                [
                    "--spec-draft-model",
                    str(self.spec_draft_model),
                    "--spec-type",
                    "draft-mtp",
                    "--spec-draft-n-max",
                    str(self.spec_draft_n_max),
                ]
            )
        self._server = LlamaServerProcess(
            model_path=model_path,
            server_path=server_path,
            host=host,
            port=port,
            ctx_size=ctx_size,
            n_gpu_layers=n_gpu_layers,
            extra_args=extra_args,
            log_path=log_path,
            label="transcription llama-server",
        )
        self.process = self._server.process

    def transcribe(self, chunk: AudioChunk, previous_transcript: str | None = None) -> TranscriptChunk:
        payload = self.prepare_payload(chunk)
        text = self.transcribe_payload(chunk, payload, previous_transcript)
        return TranscriptChunk(chunk=chunk, text=text)

    def prepare_payload(self, chunk: AudioChunk, previous_transcript: str | None = None) -> dict:
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
                        {"type": "text", "text": build_transcription_prompt(self.glossary, previous_transcript)},
                        {"type": "input_audio", "input_audio": {"data": audio_data, "format": "wav"}},
                    ],
                }
            ],
        }

    def transcribe_payload(self, chunk: AudioChunk, payload: dict, previous_transcript: str | None = None) -> str:
        cleaned = self._transcribe_payload_once(chunk, payload)
        if cleaned == UNTRANSCRIBABLE_AUDIO_TOKEN:
            return ""
        if cleaned and not _is_suspect_transcript(cleaned, chunk):
            return cleaned
        recovered = self._recover_with_split(chunk, depth=0)
        if recovered:
            return recovered
        if not previous_transcript:
            print(
                f"Warning: contextual retry skipped for chunk {chunk.index}; preceding transcript unavailable.",
                flush=True,
            )
            return ""
        print(f"Retrying chunk {chunk.index} with preceding transcript context...", flush=True)
        contextual_payload = self.prepare_payload(chunk, previous_transcript)
        contextual = self._transcribe_payload_once(chunk, contextual_payload)
        if contextual == UNTRANSCRIBABLE_AUDIO_TOKEN:
            return ""
        if contextual and not _is_suspect_transcript(contextual, chunk) and not _repeats_context(contextual, previous_transcript):
            return contextual
        print(f"Warning: contextual transcription failed for chunk {chunk.index}; giving up.", flush=True)
        return ""

    def _recover_with_split(self, chunk: AudioChunk, depth: int) -> str:
        if depth >= self.max_transcription_split_depth:
            return ""
        subchunks = split_chunk_with_tighter_vad(
            chunk,
            sample_rate=16000,
            temp_dir=self.temp_dir,
            keep_temp=True,
            session=self.vad_session,
        )
        if len(subchunks) < 2:
            return ""
        print(
            f"Warning: unusable transcription for chunk {chunk.index} "
            f"[{chunk.start:.2f}-{chunk.end:.2f}s]; retrying as {len(subchunks)} subchunks.",
            flush=True,
        )
        parts: list[str] = []
        for subchunk in subchunks:
            subpayload = self.prepare_payload(subchunk)
            part = self._transcribe_payload_once(subchunk, subpayload)
            if part == UNTRANSCRIBABLE_AUDIO_TOKEN:
                continue
            if not part or _is_suspect_transcript(part, subchunk):
                part = self._recover_with_split(subchunk, depth + 1)
            if part:
                parts.append(part)
        merged = _merge_transcript_parts(parts)
        if merged and not _is_suspect_transcript(merged, chunk):
            return merged
        print(f"Warning: tighter-VAD recovery exhausted for chunk {chunk.index}.", flush=True)
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
        self._server.close()

def _merge_transcript_parts(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if part.strip()]
    return "".join(cleaned)


def _repeats_context(text: str, previous_transcript: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    previous = re.sub(r"\s+", "", previous_transcript)
    if not normalized or not previous:
        return False
    maximum = min(len(normalized), len(previous), 24)
    return any(normalized[:overlap] == previous[-overlap:] for overlap in range(maximum, 7, -1))


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
