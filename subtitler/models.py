"""Shared data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class AudioChunk:
    index: int
    start: float
    end: float
    samples: Any
    wav_path: Path | None = None
    vad_activation: float = 0.0
    vad_peak: float = 0.0


@dataclass
class TranscriptChunk:
    chunk: AudioChunk
    text: str


@dataclass
class AlignedToken:
    text: str
    start: float
    end: float
    kind: Literal["word", "char", "token"] = "token"


@dataclass
class AlignedChunk:
    chunk: AudioChunk
    text: str
    tokens: list[AlignedToken]
    fallback: bool = False


@dataclass
class Subtitle:
    start_time: float
    end_time: float
    text: str
    tokens: list[AlignedToken] = field(default_factory=list)
    alignment_fallback: bool = False
    chain_index: int | None = None
    chain_part_index: int | None = None
    cleanup_group_index: int | None = None
    split_source: str = ""
    timing_adjustment: str = "none"


@dataclass
class ExoMarker:
    start_time: float
    end_time: float
    text: str = ""


@dataclass
class ChapterSuggestion:
    start_subtitle_index: int
    end_subtitle_index: int
    title: str
    previous_topic: str = ""
    next_topic: str = ""


@dataclass
class MisTranscriptionFlag:
    line_number: int
    text: str
    reason: str = ""


@dataclass
class ExoSettings:
    width: int = 2560
    height: int = 1440
    rate: int = 60
    scale: int = 1
    audio_rate: int = 48000
    audio_ch: int = 2
    font: str = "M+ 2p heavy"
    font_size: int = 60
    text_color: str = "ffffff"
    y_position: float = 717.0


@dataclass
class SplitPlanResult:
    lines: list[str] | None
    raw_line_count: int = 0
    clean_line_count: int = 0
    accepted: bool = False
    reject_reason: str = "none"
    input_text: str = ""
    raw_response: str = ""
    cleaned_lines: list[str] = field(default_factory=list)
    sentence_break_count: int = 0
    connective_break_count: int = 0
    partial_lines: list[str] = field(default_factory=list)
    partial_rejected_lines: list[str] = field(default_factory=list)
    partial_accept_count: int = 0
    partial_reject_count: int = 0
    accepted_prefix_chars: int = 0
    remaining_text_after_partial: str = ""
