"""Source-timed evidence contracts for content analysis operations.

Coordinates use milliseconds on the original source timeline. Transcript
conversion requires a complete raw subtitle document, never cleaned display text.
"""

from dataclasses import dataclass
from pathlib import Path

from .timed_text import load_timed_text


@dataclass(frozen=True)
class TranscriptEvidence:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class VisualEvidence:
    start_ms: int
    end_ms: int
    description: str
    tags: tuple[str, ...] = ()
    confidence: float = 0.0
    motion_level: float | None = None
    visual_category: str = "other"
    observed_label: str = ""


def load_transcript_evidence(document_path: Path) -> list[TranscriptEvidence]:
    """Adapt validated source-timed text into the editorial evidence model."""
    document = load_timed_text(document_path, require_complete_raw=True)
    return [
        TranscriptEvidence(round(span.start_sec * 1000), round(span.end_sec * 1000), span.text.strip())
        for span in document.spans
        if span.text.strip() and round(span.end_sec * 1000) > round(span.start_sec * 1000)
    ]

