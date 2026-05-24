"""Lightweight CSV profiling for the subtitle pipeline."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter


def now() -> float:
    return perf_counter()


@dataclass
class ChunkProfile:
    chunk_index: int
    chunk_start: float
    chunk_end: float
    payload_prepare_ms: float = 0.0
    transcribe_wait_ms: float = 0.0
    align_ms: float = 0.0
    align_worker_id: int = 0
    postprocess_ms: float = 0.0
    status: str = "ok"
    error: str = ""


@dataclass
class PipelineProfiler:
    enabled: bool
    output_path: Path | None
    rows: dict[int, ChunkProfile] = field(default_factory=dict)

    def start_chunk(self, index: int, start: float, end: float) -> None:
        if self.enabled:
            self.rows[index] = ChunkProfile(index, start, end)

    def add_ms(self, index: int, field_name: str, elapsed_ms: float) -> None:
        if not self.enabled or index not in self.rows:
            return
        current = getattr(self.rows[index], field_name)
        setattr(self.rows[index], field_name, current + elapsed_ms)

    def mark_error(self, index: int, error: Exception | str) -> None:
        if not self.enabled or index not in self.rows:
            return
        self.rows[index].status = "error"
        self.rows[index].error = str(error).replace("\r", " ").replace("\n", " ")

    def set_align_worker(self, index: int, worker_id: int) -> None:
        if not self.enabled or index not in self.rows:
            return
        self.rows[index].align_worker_id = worker_id

    def write(self) -> None:
        if not self.enabled or self.output_path is None:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "chunk_index",
                    "chunk_start",
                    "chunk_end",
                    "payload_prepare_ms",
                    "transcribe_wait_ms",
                    "align_ms",
                    "align_worker_id",
                    "postprocess_ms",
                    "status",
                    "error",
                ],
            )
            writer.writeheader()
            for index in sorted(self.rows):
                writer.writerow(self.rows[index].__dict__)


@dataclass
class RegroupProfile:
    chain_index: int
    source_chunk_indexes: str
    start: float
    end: float
    duration_sec: float
    chunk_count: int
    token_count: int
    fallback: bool
    split_count: int
    reason_closed: str
    max_internal_chunk_gap: float = 0.0
    avg_internal_chunk_gap: float = 0.0
    gap_sec_used: float = 0.0


@dataclass
class LlmSplitProfile:
    chain_index: int
    attempt_index: int
    input_chars: int
    input_tokens: int
    max_chars: int
    raw_line_count: int
    clean_line_count: int
    accepted: bool
    reject_reason: str
    output_line_count: int
    pass_name: str = ""
    partial_accept_count: int = 0
    partial_reject_count: int = 0
    accepted_prefix_chars: int = 0
    remaining_chars_after_partial: int = 0
    input_preview: str = ""
    sentence_break_count: int = 0
    connective_break_count: int = 0


def write_regroup_profile(path: Path, rows: list[RegroupProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "chain_index",
                "source_chunk_indexes",
                "start",
                "end",
                "duration_sec",
                "chunk_count",
                "token_count",
                "fallback",
                "split_count",
                "reason_closed",
                "max_internal_chunk_gap",
                "avg_internal_chunk_gap",
                "gap_sec_used",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_llm_split_profile(path: Path, rows: list[LlmSplitProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "chain_index",
                "attempt_index",
                "input_chars",
                "input_tokens",
                "max_chars",
                "raw_line_count",
                "clean_line_count",
                "accepted",
                "reject_reason",
                "output_line_count",
                "pass_name",
                "partial_accept_count",
                "partial_reject_count",
                "accepted_prefix_chars",
                "remaining_chars_after_partial",
                "input_preview",
                "sentence_break_count",
                "connective_break_count",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_llm_split_rejections(
    path: Path,
    rows: list[tuple[LlmSplitProfile, str, str, list[str], list[str], list[str]]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for index, (row, input_text, raw_response, cleaned_lines, accepted_lines, rejected_lines) in enumerate(rows, start=1):
            handle.write(f"=== Rejected LLM split attempt {index} ===\n")
            handle.write(f"chain_index: {row.chain_index}\n")
            handle.write(f"attempt_index: {row.attempt_index}\n")
            handle.write(f"reject_reason: {row.reject_reason}\n")
            handle.write(f"input_chars: {row.input_chars}\n")
            handle.write(f"input_tokens: {row.input_tokens}\n")
            handle.write(f"max_chars: {row.max_chars}\n")
            handle.write(f"raw_line_count: {row.raw_line_count}\n")
            handle.write(f"clean_line_count: {row.clean_line_count}\n")
            handle.write("\n--- Input text ---\n")
            handle.write(input_text)
            handle.write("\n\n--- Raw LLM response ---\n")
            handle.write(raw_response or "<empty>")
            handle.write("\n\n--- Cleaned response lines ---\n")
            if cleaned_lines:
                for line_number, line in enumerate(cleaned_lines, start=1):
                    handle.write(f"{line_number}. {line}\n")
            else:
                handle.write("<none>\n")
            handle.write("\n--- Accepted prefix lines ---\n")
            if accepted_lines:
                for line_number, line in enumerate(accepted_lines, start=1):
                    handle.write(f"{line_number}. {line}\n")
            else:
                handle.write("<none>\n")
            handle.write("\n--- Rejected/unused LLM lines ---\n")
            if rejected_lines:
                for line_number, line in enumerate(rejected_lines, start=1):
                    handle.write(f"{line_number}. {line}\n")
            else:
                handle.write("<none>\n")
            handle.write("\n")


@dataclass
class SubtitleTimingProfile:
    subtitle_index: int
    chain_index: int | str
    chain_part_index: int | str
    start: float
    end: float
    duration: float
    text_chars: int
    token_count: int
    first_token_start: float | str
    last_token_end: float | str
    prev_end: float | str
    gap_from_prev: float | str
    same_chain_as_prev: bool
    source: str
    timing_adjustment: str


@dataclass
class BoundaryTimingProfile:
    subtitle_index: int
    chain_index: int | str
    previous_text_tail: str
    next_text_head: str
    previous_token_text: str
    previous_token_start: float | str
    previous_token_end: float | str
    next_token_text: str
    next_token_start: float | str
    next_token_end: float | str
    original_next_start: float
    adjusted_next_start: float
    pull_sec: float
    lead_in_sec: float
    boundary: float


def write_subtitle_timing_profile(path: Path, rows: list[SubtitleTimingProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "subtitle_index",
                "chain_index",
                "chain_part_index",
                "start",
                "end",
                "duration",
                "text_chars",
                "token_count",
                "first_token_start",
                "last_token_end",
                "prev_end",
                "gap_from_prev",
                "same_chain_as_prev",
                "source",
                "timing_adjustment",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_boundary_timing_profile(path: Path, rows: list[BoundaryTimingProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "subtitle_index",
                "chain_index",
                "previous_text_tail",
                "next_text_head",
                "previous_token_text",
                "previous_token_start",
                "previous_token_end",
                "next_token_text",
                "next_token_start",
                "next_token_end",
                "original_next_start",
                "adjusted_next_start",
                "pull_sec",
                "lead_in_sec",
                "boundary",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
