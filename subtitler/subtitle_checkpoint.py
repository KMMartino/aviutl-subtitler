"""Recover source-timed subtitle work using operation-specific input signatures."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .api_usage import ApiUsageLedger, ApiUsageRow
from .artifact_io import write_json_artifact
from .errors import SubtitlerError
from .models import AlignedToken, ExoMarker, Subtitle
from .subtitle_stage import SubtitleStageOutcome
from .transcript_document import TranscriptDocument, load_transcript_document, write_transcript_document

# Increment when transcription or subtitle planning semantics change.
PREPARATION_VERSION = 3


@dataclass(frozen=True)
class SubtitleCheckpoint:
    path: Path
    revision_id: str
    signature: str
    transcript_path: Path
    transcript: TranscriptDocument
    subtitles: SubtitleStageOutcome | None
    usage: list[ApiUsageRow]
    subtitle_signature: str | None = None

    def finish(self) -> None:
        # Retain the completed revision for downstream reprocessing and export.
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("revision_id") != self.revision_id:
            raise SubtitlerError("Pending run was replaced by another run")
        value["state"] = "complete"
        write_json_artifact(self.path, value)


def preparation_signature(settings: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        {"version": PREPARATION_VERSION, **settings}, sort_keys=True,
        ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def save_subtitle_checkpoint(
    path: Path, *, signature: str, transcript_path: Path,
    subtitles: SubtitleStageOutcome | None, usage: ApiUsageLedger, subtitle_signature: str | None = None,
) -> SubtitleCheckpoint:
    transcript = load_transcript_document(transcript_path)
    transcript.require_reusable(Path(transcript.source_path), transcript.audio_track)
    if subtitles is not None:
        decode_subtitle_plan(asdict(subtitles))
    revision = uuid4().hex
    # The public transcript export can be overwritten by a different workflow.
    # Keep this run's dependency in its own revision directory.
    transcript_path = path.with_suffix(".revisions") / revision / "transcript.json"
    write_transcript_document(transcript_path, transcript)
    write_json_artifact(path, {
        "type": "subtitle_checkpoint", "schema_version": 2, "state": "pending",
        "revision_id": revision, "signature": signature,
        "transcript_path": str(transcript_path.resolve()), "transcript_revision": transcript.revision_id,
        "subtitle_signature": subtitle_signature,
        "subtitles": asdict(subtitles) if subtitles is not None else None, "usage": [asdict(row) for row in usage.rows],
    })
    return SubtitleCheckpoint(path, revision, signature, transcript_path, transcript, subtitles, list(usage.rows), subtitle_signature)


def load_subtitle_checkpoint(
    path: Path, *, signature: str, source_path: Path, audio_track: int, subtitle_signature: str | None = None,
) -> SubtitleCheckpoint | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("type") != "subtitle_checkpoint" or value.get("schema_version") != 2:
            raise ValueError("unsupported subtitle checkpoint")
        if value.get("signature") != signature:
            return None
        if value.get("state") not in ("pending", "complete") or not isinstance(value.get("revision_id"), str) or not value["revision_id"]:
            raise ValueError("invalid pending run identity")
        transcript_path = Path(value["transcript_path"])
        transcript = load_transcript_document(transcript_path)
        if transcript.revision_id != value["transcript_revision"]:
            raise ValueError("the referenced transcript revision was replaced")
        transcript.require_reusable(source_path, audio_track)
        subtitles = decode_subtitle_plan(value["subtitles"]) if value["subtitles"] is not None else None
        if value.get("subtitle_signature") != subtitle_signature:
            subtitles = None
        usage = [ApiUsageRow(**row) for row in value["usage"]]
        for row in usage:
            if type(row.cost_usd) not in (int, float) or not math.isfinite(row.cost_usd) or row.cost_usd < 0:
                raise ValueError("invalid stored API cost")
        return SubtitleCheckpoint(path, value["revision_id"], signature, transcript_path, transcript, subtitles, usage, value.get("subtitle_signature"))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Do not silently discard paid work and launch a new paid run on corruption.
        raise SubtitlerError(f"Could not resume subtitle checkpoint {path}: {exc}") from exc


def decode_subtitle_plan(value: Any) -> SubtitleStageOutcome:
    subtitles = [Subtitle(**{**item, "tokens": [AlignedToken(**token) for token in item["tokens"]]})
                 for item in value["subtitles"]]
    chapters = [ExoMarker(**item) for item in value["chapter_markers"]]
    qa = [ExoMarker(**item) for item in value["mistranscription_markers"]]
    for item in [*subtitles, *chapters, *qa]:
        _validate_span(item.start_time, item.end_time, item.text)
    for subtitle in subtitles:
        if type(subtitle.alignment_fallback) is not bool:
            raise ValueError("invalid subtitle alignment flag")
        for index in (subtitle.chain_index, subtitle.chain_part_index, subtitle.cleanup_group_index):
            if index is not None and (type(index) is not int or index < 0):
                raise ValueError("invalid subtitle grouping index")
        if not isinstance(subtitle.split_source, str) or not isinstance(subtitle.timing_adjustment, str):
            raise ValueError("invalid subtitle timing provenance")
        if subtitle.outline_color is not None and not isinstance(subtitle.outline_color, str):
            raise ValueError("invalid subtitle outline color")
        for token in subtitle.tokens:
            _validate_span(token.start, token.end, token.text)
            if token.kind not in ("word", "char", "token"):
                raise ValueError("invalid aligned token kind")
    for marker in [*chapters, *qa]:
        if marker.group_id is not None and (type(marker.group_id) is not int or marker.group_id < 0):
            raise ValueError("invalid marker group")
    return SubtitleStageOutcome(subtitles, chapters, qa)


def _validate_span(start: Any, end: Any, text: Any) -> None:
    if not isinstance(text, str) or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (start, end)) or end < start:
        raise ValueError("invalid source-timed subtitle or marker")
