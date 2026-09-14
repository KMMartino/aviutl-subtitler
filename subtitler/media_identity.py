"""Lightweight recording fingerprints shared by processing workflows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import SubtitlerError


FINGERPRINT_ALGORITHM = "sha256-sampled-v1"


@dataclass(frozen=True)
class SourceFingerprint:
    algorithm: str
    size_bytes: int
    digest: str
    sample_size_bytes: int


def fingerprint_source(path: Path, *, sample_size: int = 1024 * 1024) -> SourceFingerprint:
    """Fingerprint large media without reading the full file.

    The digest is independent of filename and timestamps so moved or renamed
    source media can be relinked. Size plus evenly distributed samples guard
    against accidentally accepting a different recording with the same name.
    """
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SubtitlerError(f"Could not inspect editorial source {path}: {exc}") from exc
    if not path.is_file():
        raise SubtitlerError(f"Editorial source is not a file: {path}")

    hasher = hashlib.sha256()
    hasher.update(FINGERPRINT_ALGORITHM.encode("ascii"))
    hasher.update(size.to_bytes(16, "big", signed=False))
    offsets = _sample_offsets(size, sample_size)
    try:
        with path.open("rb") as handle:
            for offset in offsets:
                handle.seek(offset)
                data = handle.read(min(sample_size, size - offset))
                hasher.update(offset.to_bytes(16, "big", signed=False))
                hasher.update(len(data).to_bytes(8, "big", signed=False))
                hasher.update(data)
    except OSError as exc:
        raise SubtitlerError(f"Could not fingerprint editorial source {path}: {exc}") from exc
    return SourceFingerprint(
        algorithm=FINGERPRINT_ALGORITHM,
        size_bytes=size,
        digest=hasher.hexdigest(),
        sample_size_bytes=sample_size,
    )


def _sample_offsets(size: int, sample_size: int) -> list[int]:
    if size <= sample_size * 3:
        return [0]
    last = size - sample_size
    return sorted({0, max(0, (size - sample_size) // 2), last})


