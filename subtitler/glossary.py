"""Plain-text glossary support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    guidance: str = ""


def find_glossary(input_path: Path, explicit: Path | None, disabled: bool, project_dir: Path) -> Path | None:
    if disabled:
        return None
    if explicit is not None:
        return explicit
    beside_input = input_path.with_name("glossary.txt")
    if beside_input.exists():
        return beside_input
    project_glossary = project_dir / "glossary.txt"
    if project_glossary.exists():
        return project_glossary
    return None


def load_glossary(path: Path | None, limit: int = 80) -> list[GlossaryEntry]:
    if path is None:
        return []
    if not path.exists():
        print(f"Warning: glossary file not found: {path}")
        return []
    entries: list[GlossaryEntry] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        term = parts[0] if parts else ""
        guidance = parts[1] if len(parts) > 1 else ""
        if term:
            entries.append(GlossaryEntry(term=term, guidance=guidance))
        if len(entries) >= limit:
            break
    return entries


def format_glossary(entries: list[GlossaryEntry]) -> str:
    lines = []
    for entry in entries:
        if entry.guidance:
            lines.append(f"{entry.term} | {entry.guidance}")
        else:
            lines.append(entry.term)
    return "\n".join(lines)
