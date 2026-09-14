"""Shared locale rules for generated editorial material."""

from __future__ import annotations

from typing import Any, Literal, cast


EditorialLocale = Literal["en", "ja"]


def editorial_locale(value: Any) -> EditorialLocale:
    return cast(EditorialLocale, value if value in {"en", "ja"} else "en")


def output_language_instruction(value: Any) -> str:
    if editorial_locale(value) == "ja":
        return (
            "Write every human-facing free-text JSON value in natural, concise Japanese. "
            "Keep JSON keys, enum values, IDs, filenames, and exact quoted transcript phrases unchanged. "
            "Do not translate verbatim transcript excerpts."
        )
    return (
        "Write every human-facing free-text JSON value in natural, concise English. "
        "Keep JSON keys, enum values, IDs, filenames, and exact quoted transcript phrases unchanged. "
        "Do not translate verbatim transcript excerpts."
    )


def locale_label(value: Any, english: str, japanese: str) -> str:
    return japanese if editorial_locale(value) == "ja" else english


def processing_language_instruction(project: dict[str, Any], production_fields: tuple[str, ...] = ()) -> str:
    """Separate generated analytical prose from production copy and verbatim evidence."""
    language = locale_label(project.get("processing_locale", "en"), "English", "Japanese")
    instruction = (
        f"Write generated intermediate analysis, descriptions, summaries, labels, uncertainty, and decision reasons in natural {language}. "
        "Keep JSON keys, enum values, IDs, timestamps, proper names, and exact source quotations unchanged. "
        "Verbatim speech and semantic utterance meanings must remain in the original spoken language. "
    )
    if production_fields:
        output = locale_label(project.get("output_locale", "en"), "English", "Japanese")
        instruction += f"Exception: human production guidance fields {', '.join(production_fields)} must be in natural {output}. "
    return instruction
