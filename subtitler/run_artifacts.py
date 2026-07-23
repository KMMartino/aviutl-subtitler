"""Paths and writers for user-requested run artifacts and diagnostics."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from .api_usage import ApiUsageLedger
from .models import ExoMarker


class RunArguments(Protocol):
    input: str
    output: str | None
    workflow: str


@dataclass(frozen=True)
class RunArtifactPaths:
    """All optional sidecar paths derived from one output name."""

    directory: Path | None
    base: Path | None
    profile: Path | None
    run_metadata: Path | None
    api_usage: Path | None
    aligned_text: Path | None
    final_text: Path | None
    cleanup_diff: Path | None
    mistranscriptions: Path | None
    regroup_profile: Path | None
    llm_split_profile: Path | None
    subtitle_timing_profile: Path | None
    boundary_timing_profile: Path | None
    planning_profile: Path | None
    chapter_markers: Path | None
    silence_cuts: Path | None


def build_run_artifact_paths(
    input_path: Path,
    output_path: Path,
    *,
    enabled: bool,
    directory: Path | None = None,
) -> RunArtifactPaths:
    """Build the complete sidecar path set, creating only its parent directory."""
    sidecar_dir = (directory or input_path.parent / "subtitle_files") if enabled else None
    if sidecar_dir is not None:
        sidecar_dir.mkdir(parents=True, exist_ok=True)
    base = sidecar_dir / output_path.stem if sidecar_dir is not None else None

    def suffix(value: str) -> Path | None:
        return base.with_suffix(value) if base is not None else None

    return RunArtifactPaths(
        directory=sidecar_dir,
        base=base,
        profile=suffix(".profile.csv"),
        run_metadata=suffix(".run.json"),
        api_usage=suffix(".api_usage.csv"),
        aligned_text=suffix(".aligned_text.txt"),
        final_text=suffix(".final_text.txt"),
        cleanup_diff=suffix(".cleanup_diff.txt"),
        mistranscriptions=suffix(".possible_mistranscriptions.txt"),
        regroup_profile=suffix(".regroup.csv"),
        llm_split_profile=suffix(".llm_split.csv"),
        subtitle_timing_profile=suffix(".subtitle_timing.csv"),
        boundary_timing_profile=suffix(".boundary_timing.csv"),
        planning_profile=suffix(".planning.csv"),
        chapter_markers=suffix(".youtube_chapters.json"),
        silence_cuts=suffix(".silence_cuts.json"),
    )


def write_aligned_text(path: Path, aligned: Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in sorted(aligned, key=lambda chunk: (chunk.chunk.start, chunk.chunk.end)):
        lines.append(
            f"[chunk {item.chunk.index} {item.chunk.start:.3f}-{item.chunk.end:.3f} "
            f"fallback={item.fallback} tokens={len(item.tokens)}]"
        )
        lines.append(item.text.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_final_subtitle_text(path: Path, subtitles: Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{index}. {sub.text}" for index, sub in enumerate(subtitles, start=1)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def flag_possible_mistranscriptions(
    subtitles: Sequence[Any], refiner: Any, output_path: Path
) -> list[ExoMarker]:
    numbered_lines = [(index, sub.text) for index, sub in enumerate(subtitles, start=1)]
    flags = refiner.flag_mistranscriptions(numbered_lines)
    raw_response = getattr(refiner, "last_mistranscription_raw", "")
    by_line: dict[int, list[str]] = {}
    markers: list[ExoMarker] = []
    marked_lines: set[int] = set()
    for flag in flags:
        line_index = flag.line_number - 1
        if line_index < 0 or line_index >= len(subtitles):
            continue
        sub = subtitles[line_index]
        if flag.text not in sub.text:
            continue
        reason = flag.reason.strip() or "review candidate"
        severity = flag.severity if flag.severity in {"high", "medium", "low"} else "medium"
        by_line.setdefault(flag.line_number, []).append(
            f"{flag.line_number}\t{severity}\t{flag.text}\t{reason}"
        )
        if severity in {"high", "medium"} and flag.line_number not in marked_lines:
            markers.append(
                ExoMarker(sub.start_time, sub.end_time, f"{flag.line_number}: {severity} - {reason}")
            )
            marked_lines.add(flag.line_number)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_lines = []
    for line_number in sorted(by_line):
        output_lines.extend(by_line[line_number])
    output_path.write_text(
        "\n".join(output_lines) + ("\n" if output_lines else "NONE\n"), encoding="utf-8"
    )
    if raw_response:
        raw_path = output_path.with_name(f"{output_path.stem}.raw.txt")
        raw_path.write_text(raw_response + "\n", encoding="utf-8")
        print(f"Wrote raw mistranscription review: {raw_path}", flush=True)
    print(f"Possible mistranscription markers: {len(markers)}", flush=True)
    print(f"Wrote possible mistranscriptions: {output_path}", flush=True)
    return markers


def build_youtube_chapter_markers(
    subtitles: Sequence[Any], refiner: Any, output_path: Path | None
) -> list[ExoMarker]:
    if not subtitles:
        return []
    numbered = [
        (index, sub.start_time, sub.end_time, sub.text)
        for index, sub in enumerate(subtitles, start=1)
    ]
    try:
        chapters = refiner.suggest_chapters(numbered)
    except Exception as exc:
        print(
            f"Warning: YouTube chapter generation failed; continuing without chapter markers. {exc}",
            flush=True,
        )
        return []

    markers: list[ExoMarker] = []
    diagnostics: list[dict[str, Any]] = []
    for chapter in chapters:
        start_index = chapter.start_subtitle_index - 1
        end_index = chapter.end_subtitle_index - 1
        if start_index < 0 or start_index >= len(subtitles) or end_index < start_index:
            continue
        end_index = min(end_index, len(subtitles) - 1)
        start_subtitle = subtitles[start_index]
        end_subtitle = subtitles[end_index]
        title = chapter.title.strip()
        if not title:
            continue
        marker = ExoMarker(start_subtitle.start_time, end_subtitle.end_time, title)
        markers.append(marker)
        diagnostics.append(
            {
                "start_subtitle_index": chapter.start_subtitle_index,
                "end_subtitle_index": chapter.end_subtitle_index,
                "start_time": marker.start_time,
                "end_time": marker.end_time,
                "title": chapter.title,
                "previous_topic": chapter.previous_topic,
                "next_topic": chapter.next_topic,
            }
        )

    if output_path is not None:
        write_youtube_chapter_diagnostics(output_path, refiner, diagnostics)
    if markers:
        print(f"YouTube chapter markers: {len(markers)}", flush=True)
        if output_path is not None:
            print(f"Wrote YouTube chapter diagnostics: {output_path}", flush=True)
    return markers


def write_youtube_chapter_diagnostics(
    output_path: Path, refiner: Any, chapters: list[dict[str, Any]]
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": getattr(refiner, "provider", ""),
        "model": getattr(refiner, "model", ""),
        "chapters": chapters,
        "cuts": getattr(refiner, "last_youtube_chapter_cuts", []),
        "raw_response": getattr(refiner, "last_youtube_chapters_raw", ""),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_run_metadata(
    path: Path,
    args: RunArguments,
    config: dict[str, Any],
    env_path: Path,
    loaded_env_keys: list[str],
    backend_result: Any,
    api_usage: ApiUsageLedger,
    api_usage_path: Path,
    elapsed_run_seconds: float,
    *,
    argv: Sequence[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    api_key_names = ["OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPGRAM_API_KEY"]
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "output": args.output,
        "workflow": args.workflow,
        "config": config,
        "backend": {
            "name": backend_result.backend_name,
            "model": backend_result.model_name,
            "status": backend_result.status,
            "capabilities": backend_result.capabilities.__dict__,
            "metadata": backend_result.metadata,
            "diagnostics": [item.__dict__ for item in backend_result.diagnostics],
        },
        "actual_api_cost_usd": api_usage.total_cost_usd,
        "actual_api_total_tokens": api_usage.total_tokens,
        "api_usage_path": str(api_usage_path),
        "api_usage_by_provider_model": api_usage.by_provider_model(),
        "elapsed_run_seconds": elapsed_run_seconds,
        "elapsed_run_display": format_elapsed(elapsed_run_seconds),
        "env_file": str(env_path),
        "env_file_loaded": bool(loaded_env_keys),
        "env_keys_loaded": sorted(loaded_env_keys),
        "api_keys_present": {name: bool(os.environ.get(name)) for name in api_key_names},
        "argv": list(sys.argv[1:] if argv is None else argv),
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
