"""Application-specific exceptions."""


class SubtitlerError(Exception):
    """Base exception for user-facing pipeline errors."""


class AudioExtractionError(SubtitlerError):
    """Audio extraction or loading failed."""


class ModelLoadError(SubtitlerError):
    """A model could not be loaded."""


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
