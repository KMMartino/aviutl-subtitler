"""Application-specific exceptions."""


class SubtitlerError(Exception):
    """Base exception for user-facing pipeline errors."""


class AudioExtractionError(SubtitlerError):
    """Audio extraction or loading failed."""


class ModelLoadError(SubtitlerError):
    """A model could not be loaded."""


class StructuredOutputIncompleteError(ModelLoadError):
    """A hosted structured response ended before a complete result was available."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class TranscriptionError(SubtitlerError):
    """Transcription failed."""


class OutOfMemoryError(TranscriptionError):
    """Inference appears to have run out of memory."""


class VadError(SubtitlerError):
    """Voice activity detection failed."""


class AlignmentError(SubtitlerError):
    """Forced alignment failed."""


class ExoWriteError(SubtitlerError):
    """EXO generation or writing failed."""
