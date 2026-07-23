"""Normalized transcription backend contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol


TimingKind = Literal["none", "segment", "word", "char", "token", "mixed"]
BackendStatus = Literal["ok", "partial", "failed"]


@dataclass
class TranscriptToken:
    text: str
    start: float | None = None
    end: float | None = None
    kind: Literal["word", "char", "token"] = "token"
    confidence: float | None = None
    speaker: str | None = None
    language: str | None = None
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptSegment:
    index: int
    text: str
    start: float
    end: float
    tokens: list[TranscriptToken] = field(default_factory=list)
    speaker: str | None = None
    language: str | None = None
    confidence: float | None = None
    timing_kind: TimingKind = "segment"
    fallback_timing: bool = False
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeechRegion:
    index: int
    start: float
    end: float
    selected_for_transcription: bool = True
    activation: float | None = None
    peak: float | None = None
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawVadSpeechInterval:
    """An unpadded speech interval from the initial VAD inference."""

    start: float
    end: float


@dataclass
class BackendCapability:
    provides_vad: bool = False
    provides_segment_timestamps: bool = True
    provides_token_timestamps: bool = False
    provides_word_timestamps: bool = False
    provides_char_timestamps: bool = False
    provides_diarization: bool = False
    requires_external_alignment: bool = False
    supports_long_stream_selection: bool = False
    supports_glossary: bool = False
    supports_language_hint: bool = True


@dataclass
class BackendDiagnostic:
    level: Literal["info", "warning", "error"]
    message: str
    segment_index: int | None = None
    region_index: int | None = None
    code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendTranscriptResult:
    backend_name: str
    backend_version: str = ""
    model_name: str = ""
    status: BackendStatus = "ok"
    language: str = ""
    duration_sec: float = 0.0
    segments: list[TranscriptSegment] = field(default_factory=list)
    speech_regions: list[SpeechRegion] = field(default_factory=list)
    raw_vad_speech_intervals: list[RawVadSpeechInterval] = field(default_factory=list)
    diagnostics: list[BackendDiagnostic] = field(default_factory=list)
    capabilities: BackendCapability = field(default_factory=BackendCapability)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptionRequest:
    input_path: Path
    wav_path: Path
    duration_sec: float
    sample_rate: int
    language: str
    temp_dir: Path
    sidecar_base: Path | None
    glossary: list[Any]
    profile_enabled: bool = False
    workflow: str = "local"
    metadata: dict[str, Any] = field(default_factory=dict)


class TranscriptionBackend(Protocol):
    name: str
    capabilities: BackendCapability

    def transcribe(self, request: TranscriptionRequest) -> BackendTranscriptResult:
        ...
