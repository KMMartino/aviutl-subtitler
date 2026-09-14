"""Definitions for the initial code-composed product workflows."""

from dataclasses import dataclass

from .errors import SubtitlerError


@dataclass(frozen=True)
class SubtitlePolicy:
    raw_transcript: bool = False
    allow_chapters: bool = False


@dataclass(frozen=True)
class WorkflowDefinition:
    engine: str
    process: str
    transcript_scope: str
    output_suffix: str
    subtitles: SubtitlePolicy
    capabilities: frozenset[str] = frozenset()


WORKFLOW_DEFINITIONS = {
    "local": WorkflowDefinition("local", "subtitles", "full", "", SubtitlePolicy(), frozenset({"silence"})),
    "hosted": WorkflowDefinition("hosted", "subtitles", "full", "-hosted", SubtitlePolicy(allow_chapters=True),
                                 frozenset({"silence", "broll", "chapters"})),
    "hosted-long-stream": WorkflowDefinition("hosted", "editorial", "long-stream", "-long-stream-hosted",
                                             SubtitlePolicy(raw_transcript=True)),
}


def workflow_definition(name: str) -> WorkflowDefinition:
    try:
        return WORKFLOW_DEFINITIONS[name]
    except KeyError:
        raise SubtitlerError(f"Unavailable workflow: {name}") from None
