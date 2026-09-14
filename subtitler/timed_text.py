"""Versioned timed-text handoff; timestamps always use original source seconds.

This document stores text and timing together. It is independent of diagnostic
CSV formatting and can be consumed without the source media or a running model.
It represents a subtitle plan, not the complete aligned transcription artifact.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .errors import SubtitlerError
from .artifact_io import write_json_artifact
from .models import Subtitle


@dataclass(frozen=True)
class TimedTextSpan:
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True)
class TimedTextDocument:
    revision_id: str
    source_path: str
    audio_track: int
    raw_transcript: bool
    complete: bool
    spans: tuple[TimedTextSpan, ...]
    input_revision_id: str | None = None

    @classmethod
    def from_subtitles(
        cls,
        subtitles: Sequence[Subtitle],
        *,
        source_path: Path,
        audio_track: int,
        raw_transcript: bool,
        complete: bool,
        input_revision_id: str | None = None,
    ) -> TimedTextDocument:
        return cls(
            revision_id=uuid4().hex,
            source_path=str(source_path.resolve()),
            audio_track=audio_track,
            raw_transcript=raw_transcript,
            complete=complete,
            spans=tuple(TimedTextSpan(item.start_time, item.end_time, item.text) for item in subtitles),
            input_revision_id=input_revision_id,
        )

    def validate(self) -> None:
        if not isinstance(self.revision_id, str) or not self.revision_id.strip():
            raise ValueError("missing timed-text revision")
        if not isinstance(self.source_path, str) or not self.source_path.strip():
            raise ValueError("missing timed-text source")
        if type(self.audio_track) is not int or self.audio_track < 0:
            raise ValueError("invalid audio track")
        if type(self.raw_transcript) is not bool or type(self.complete) is not bool:
            raise ValueError("invalid timed-text policy/completeness")
        if self.input_revision_id is not None and (not isinstance(self.input_revision_id, str) or not self.input_revision_id):
            raise ValueError("invalid input revision")
        previous_start = -1.0
        for span in self.spans:
            if not isinstance(span.text, str):
                raise ValueError("invalid timed-text content")
            for value in (span.start_sec, span.end_sec):
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("invalid source timestamp")
            if span.end_sec < span.start_sec or span.start_sec < previous_start:
                raise ValueError("reversed or unordered source timestamps")
            previous_start = span.start_sec


def write_timed_text(path: Path, document: TimedTextDocument) -> None:
    """Atomically replace a document; a failed write leaves the old file intact."""
    document.validate()
    payload = {"type": "subtitle_document", "schema_version": 1, "time_base": "source_seconds", **asdict(document)}
    write_json_artifact(path, payload)


def load_timed_text(path: Path, *, require_complete_raw: bool = False) -> TimedTextDocument:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or (
            payload.get("type") != "subtitle_document"
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
            or payload.get("time_base") != "source_seconds"
        ):
            raise ValueError("unsupported timed-text contract")
        if not isinstance(payload["spans"], list):
            raise ValueError("invalid timed-text spans")
        document = TimedTextDocument(
            revision_id=payload["revision_id"],
            source_path=payload["source_path"],
            audio_track=payload["audio_track"],
            raw_transcript=payload["raw_transcript"],
            complete=payload["complete"],
            spans=tuple(TimedTextSpan(**item) for item in payload["spans"]),
            input_revision_id=payload.get("input_revision_id"),
        )
        document.validate()
        if require_complete_raw and (not document.complete or not document.raw_transcript):
            raise ValueError("editorial evidence requires a complete raw transcript")
        return document
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise SubtitlerError(f"Could not load timed-text artifact {path}: {exc}") from exc
