"""Durable aligned transcription, reusable by independent downstream workflows.

All times are seconds on the original source timeline. Runtime audio buffers
are excluded. Explicit reuse checks the source fingerprint, modification time,
and audio track, then reconstructs fresh processing objects.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifact_io import write_json_artifact
from .errors import SubtitlerError
from .media_identity import FINGERPRINT_ALGORITHM, fingerprint_source
from .models import AlignedToken
from .transcript_normalizer import backend_result_to_aligned_chunks
from .transcription_backend import (
    BackendCapability, BackendDiagnostic, BackendTranscriptResult, RawVadSpeechInterval,
    SpeechRegion, TranscriptSegment, TranscriptToken,
)


@dataclass(frozen=True)
class TranscriptDocument:
    revision_id: str
    source_path: str
    source_fingerprint: dict[str, Any]
    source_modified_ns: int
    audio_track: int
    duration_sec: float
    settings: dict[str, Any]
    backend: BackendTranscriptResult

    def aligned_tokens(self) -> list[AlignedToken]:
        return [token for chunk in backend_result_to_aligned_chunks(self.backend) for token in chunk.tokens]

    def speech_activity_ms(self) -> list[tuple[int, int]]:
        return sorted(
            (round(region.start * 1000), round(region.end * 1000))
            for region in self.backend.speech_regions
            if region.selected_for_transcription and round(region.end * 1000) > round(region.start * 1000)
        )

    def validate(self) -> None:
        if not isinstance(self.revision_id, str) or not self.revision_id:
            raise ValueError("missing transcript revision")
        if not isinstance(self.source_path, str) or not self.source_path:
            raise ValueError("missing source path")
        fingerprint = self.source_fingerprint
        if not isinstance(fingerprint, dict) or fingerprint.get("algorithm") != FINGERPRINT_ALGORITHM:
            raise ValueError("unsupported source fingerprint")
        if not isinstance(fingerprint.get("digest"), str) or len(fingerprint["digest"]) != 64:
            raise ValueError("invalid source fingerprint")
        for value in (self.audio_track, self.source_modified_ns, fingerprint.get("size_bytes")):
            if type(value) is not int or value < 0:
                raise ValueError("invalid source identity")
        if fingerprint.get("sample_size_bytes") != 1024 * 1024:
            raise ValueError("unsupported fingerprint sample size")
        _range(0, self.duration_sec)
        if not isinstance(self.settings, dict):
            raise ValueError("invalid transcription settings")
        result = self.backend
        if result.status not in ("ok", "partial", "failed"):
            raise ValueError("invalid transcript completeness")
        for items in (result.segments, result.speech_regions):
            indices: set[int] = set()
            for item in items:
                _range(item.start, item.end)
                if type(item.index) is not int or item.index < 0 or item.index in indices:
                    raise ValueError("invalid or duplicate transcript segment ID")
                indices.add(item.index)
                if not isinstance(item.metadata, dict):
                    raise ValueError("invalid segment metadata")
                group = item.metadata.get("vad_group_index")
                if group is not None and (type(group) is not int or group < 0):
                    raise ValueError("invalid speech group")
        for segment in result.segments:
            if not isinstance(segment.text, str) or type(segment.fallback_timing) is not bool:
                raise ValueError("invalid transcript text")
            for token in segment.tokens:
                if not isinstance(token.text, str) or token.kind not in ("word", "char", "token"):
                    raise ValueError("invalid aligned token")
                if token.start is not None or token.end is not None:
                    _range(token.start, token.end)
        for region in result.speech_regions:
            if type(region.selected_for_transcription) is not bool:
                raise ValueError("invalid speech selection")
            for value in (region.activation, region.peak):
                if value is not None:
                    _range(0, value)
        for interval in result.raw_vad_speech_intervals:
            _range(interval.start, interval.end)
        if not isinstance(result.metadata, dict):
            raise ValueError("invalid backend metadata")

    def require_reusable(self, source_path: Path, audio_track: int) -> None:
        if self.backend.status != "ok" or any(
            item.code in ("transcription_failed", "cost_estimate_only") for item in self.backend.diagnostics
        ):
            raise SubtitlerError("Cannot reuse an incomplete transcription; transcribe the source again")
        if self.audio_track != audio_track:
            raise SubtitlerError("Transcript artifact uses a different audio track")
        if source_path.stat().st_mtime_ns != self.source_modified_ns or asdict(fingerprint_source(source_path)) != self.source_fingerprint:
            raise SubtitlerError("Transcript artifact does not match the selected source media")


def _range(start: Any, end: Any) -> None:
    for value in (start, end):
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid source timestamp")
    if end < start:
        raise ValueError("reversed source timestamps")


def write_transcript_document(path: Path, document: TranscriptDocument) -> None:
    document.validate()
    write_json_artifact(path, {
        "type": "aligned_transcript", "schema_version": 1, "time_base": "source_seconds",
        "operation_version": 1, **asdict(document),
    })


def create_transcript_document(
    *, source_path: Path, audio_track: int, duration_sec: float,
    settings: dict[str, Any], backend: BackendTranscriptResult,
) -> TranscriptDocument:
    return TranscriptDocument(
        uuid4().hex, str(source_path.resolve()), asdict(fingerprint_source(source_path)),
        source_path.stat().st_mtime_ns, audio_track, duration_sec, settings, backend,
    )


def load_transcript_document(path: Path) -> TranscriptDocument:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or (
            payload.get("type") != "aligned_transcript"
            or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
            or payload.get("time_base") != "source_seconds"
            or type(payload.get("operation_version")) is not int or payload["operation_version"] != 1
        ):
            raise ValueError("unsupported aligned-transcript contract")
        raw = payload["backend"]
        backend = BackendTranscriptResult(**{
            **raw,
            "segments": [TranscriptSegment(**{**item, "tokens": [TranscriptToken(**token) for token in item["tokens"]]})
                         for item in raw["segments"]],
            "speech_regions": [SpeechRegion(**item) for item in raw["speech_regions"]],
            "raw_vad_speech_intervals": [RawVadSpeechInterval(**item) for item in raw["raw_vad_speech_intervals"]],
            "diagnostics": [BackendDiagnostic(**item) for item in raw["diagnostics"]],
            "capabilities": BackendCapability(**raw["capabilities"]),
        })
        document = TranscriptDocument(
            payload["revision_id"], payload["source_path"], payload["source_fingerprint"],
            payload["source_modified_ns"], payload["audio_track"], payload["duration_sec"], payload["settings"], backend,
        )
        document.validate()
        return document
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SubtitlerError(f"Could not load transcript artifact {path}: {exc}") from exc
