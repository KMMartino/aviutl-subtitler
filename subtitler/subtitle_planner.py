"""Alignment-aware subtitle grouping and cleanup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import AlignedChunk, AlignedToken, ExoMarker, Subtitle
from .profiling import (
    BoundaryTimingProfile,
    LlmSplitProfile,
    RegroupProfile,
    SubtitleTimingProfile,
    write_llm_split_profile,
    write_llm_split_rejections,
    write_regroup_profile,
    write_boundary_timing_profile,
    write_subtitle_timing_profile,
)
from .splitter import split_aligned_chunk, split_token_chain
from .text_refiner import TextRefiner


@dataclass
class Chain:
    chunks: list[AlignedChunk]
    reason_closed: str
    gap_sec: float = 0.0

    @property
    def fallback(self) -> bool:
        return any(chunk.fallback or not chunk.tokens for chunk in self.chunks)


def _tokens_to_text(tokens: list[AlignedToken]) -> str:
    return "".join(token.text for token in tokens)


def _merge_window(chunks: list[AlignedChunk]) -> AlignedChunk:
    first = chunks[0]
    tokens: list[AlignedToken] = []
    text_parts: list[str] = []
    for chunk in chunks:
        tokens.extend(chunk.tokens)
        text_parts.append(chunk.text)
    merged_text = "".join(text_parts)
    return AlignedChunk(chunk=first.chunk, text=merged_text, tokens=tokens, fallback=False)


def group_aligned_chains(
    chunks: list[AlignedChunk],
    regroup: bool,
    gap_sec: float,
    _max_window_sec: float,
    _max_window_chars: int,
    ramp_start_sec: float = 0.2,
    ramp_step_sec: float = 0.1,
    ramp_max_chain_sec: float = 120.0,
    ramp_max_chain_tokens: int = 900,
) -> list[Chain]:
    ordered = sorted(chunks, key=lambda item: (item.chunk.start, item.chunk.end))
    if not regroup:
        return [Chain([chunk], "disabled", 0.0) for chunk in ordered]

    if not ordered:
        return []

    thresholds = []
    current_gap = min(ramp_start_sec, gap_sec)
    while current_gap < gap_sec:
        thresholds.append(round(current_gap, 6))
        current_gap += max(ramp_step_sec, 0.001)
    thresholds.append(gap_sec)

    active = ordered[:]
    accepted: list[Chain] = []
    for threshold in thresholds:
        is_final_threshold = threshold == thresholds[-1]
        candidates = _group_with_gap(active, threshold)
        next_active: list[AlignedChunk] = []
        for candidate in candidates:
            if candidate.fallback:
                candidate.gap_sec = threshold
                accepted.append(candidate)
                continue
            if len(candidate.chunks) <= 1 and not is_final_threshold:
                next_active.extend(candidate.chunks)
                continue
            if len(candidate.chunks) <= 1:
                candidate.gap_sec = threshold
                accepted.append(candidate)
                continue
            if _chain_too_large(candidate, ramp_max_chain_sec, ramp_max_chain_tokens):
                split_chains = _split_oversized_chain(
                    candidate,
                    ramp_max_chain_sec,
                    ramp_max_chain_tokens,
                    threshold,
                )
                accepted.extend(split_chains)
            else:
                candidate.gap_sec = threshold
                accepted.append(candidate)
        active = next_active
        if not active:
            break

    if active:
        for chunk in active:
            accepted.append(Chain([chunk], "ramp_limit", gap_sec))

    accepted.sort(key=lambda chain: (chain.chunks[0].chunk.start, chain.chunks[0].chunk.end))
    return accepted


def _group_with_gap(ordered: list[AlignedChunk], gap_sec: float) -> list[Chain]:
    if not ordered:
        return []

    groups: list[Chain] = []
    current: list[AlignedChunk] = []

    def flush(reason: str) -> None:
        nonlocal current
        if not current:
            return
        groups.append(Chain(current, reason, gap_sec))
        current = []

    for chunk in ordered:
        if chunk.fallback or not chunk.tokens:
            flush("fallback" if chunk.fallback else "tokenless")
            groups.append(Chain([chunk], "fallback" if chunk.fallback else "tokenless", gap_sec))
            continue
        if not current:
            current = [chunk]
            continue
        previous = current[-1]
        gap = chunk.chunk.start - previous.chunk.end
        if gap <= gap_sec:
            current.append(chunk)
        else:
            flush("gap")
            current = [chunk]
    flush("end")
    return groups


def _chain_too_large(chain: Chain, max_chain_sec: float, max_chain_tokens: int) -> bool:
    if max_chain_sec > 0:
        duration = chain.chunks[-1].chunk.end - chain.chunks[0].chunk.start
        if duration > max_chain_sec:
            return True
    if max_chain_tokens > 0:
        token_count = sum(len(chunk.tokens) for chunk in chain.chunks)
        if token_count > max_chain_tokens:
            return True
    return False


def _split_oversized_chain(
    chain: Chain,
    max_chain_sec: float,
    max_chain_tokens: int,
    gap_sec: float,
) -> list[Chain]:
    result: list[Chain] = []
    current: list[AlignedChunk] = []

    def would_exceed(candidate_chunks: list[AlignedChunk]) -> bool:
        if len(candidate_chunks) <= 1:
            return False
        if max_chain_sec > 0:
            duration = candidate_chunks[-1].chunk.end - candidate_chunks[0].chunk.start
            if duration > max_chain_sec:
                return True
        if max_chain_tokens > 0:
            token_count = sum(len(chunk.tokens) for chunk in candidate_chunks)
            if token_count > max_chain_tokens:
                return True
        return False

    def flush(reason: str) -> None:
        nonlocal current
        if not current:
            return
        result.append(Chain(current, reason, gap_sec))
        current = []

    for chunk in chain.chunks:
        if not current:
            current = [chunk]
            continue
        candidate = current + [chunk]
        if would_exceed(candidate):
            flush("ramp_split")
            current = [chunk]
        else:
            current = candidate
    flush("ramp_split")
    _rebalance_singleton_edges(result, max_chain_sec, max_chain_tokens, gap_sec)
    return result


def _rebalance_singleton_edges(
    chains: list[Chain],
    max_chain_sec: float,
    max_chain_tokens: int,
    gap_sec: float,
) -> None:
    if len(chains) < 2:
        return
    for index in range(len(chains)):
        if len(chains[index].chunks) != 1:
            continue
        chunk = chains[index].chunks[0]
        if index > 0:
            candidate = chains[index - 1].chunks + [chunk]
            candidate_chain = Chain(candidate, "ramp_split", gap_sec)
            if not _chain_too_large(candidate_chain, max_chain_sec, max_chain_tokens):
                chains[index - 1] = candidate_chain
                chains.pop(index)
                return
        if index + 1 < len(chains):
            candidate = [chunk] + chains[index + 1].chunks
            candidate_chain = Chain(candidate, "ramp_split", gap_sec)
            if not _chain_too_large(candidate_chain, max_chain_sec, max_chain_tokens):
                chains[index + 1] = candidate_chain
                chains.pop(index)
                return


def build_grouped_subtitles(
    chunks: list[AlignedChunk],
    max_chars: int,
    min_duration: float,
    max_duration: float,
    gap_threshold: float,
    regroup: bool,
    regroup_gap_sec: float,
    regroup_max_window_sec: float,
    regroup_max_window_chars: int,
    refiner: TextRefiner | None = None,
    cleanup_window_subtitles: int = 1,
    llm_splitter: TextRefiner | None = None,
    regroup_profile_path: Path | None = None,
    llm_split_profile_path: Path | None = None,
    llm_split_console: bool = False,
    chain_markers: list[ExoMarker] | None = None,
    subtitle_timing_profile_path: Path | None = None,
    boundary_timing_profile_path: Path | None = None,
    chain_lead_in_sec: float = 0.20,
    chain_lead_in_growth_sec: float = 0.01,
    chain_lead_in_max_sec: float = 0.60,
    regroup_ramp_start_sec: float = 0.2,
    regroup_ramp_step_sec: float = 0.1,
    regroup_ramp_max_chain_sec: float = 120.0,
    regroup_ramp_max_chain_tokens: int = 900,
    llm_max_input_chars: int = 240,
    llm_second_pass_max_input_chars: int = 240,
) -> list[Subtitle]:
    chains = group_aligned_chains(
        chunks,
        regroup=regroup,
        gap_sec=regroup_gap_sec,
        _max_window_sec=regroup_max_window_sec,
        _max_window_chars=regroup_max_window_chars,
        ramp_start_sec=regroup_ramp_start_sec,
        ramp_step_sec=regroup_ramp_step_sec,
        ramp_max_chain_sec=regroup_ramp_max_chain_sec,
        ramp_max_chain_tokens=regroup_ramp_max_chain_tokens,
    )
    subtitles: list[Subtitle] = []
    regroup_rows: list[RegroupProfile] = []
    llm_split_rows: list[LlmSplitProfile] = []
    llm_split_rejections: list[tuple[LlmSplitProfile, str, str, list[str]]] = []
    boundary_rows: list[BoundaryTimingProfile] = []
    fallback_chunks = 0
    tokenless_chunks = 0
    for chain_index, chain in enumerate(chains):
        group = _merge_window(chain.chunks) if len(chain.chunks) > 1 and not chain.fallback else chain.chunks[0]
        if group.fallback:
            fallback_chunks += 1
            split_parts = split_aligned_chunk(group, max_chars)
        elif not group.tokens:
            tokenless_chunks += 1
            split_parts = split_aligned_chunk(group, max_chars)
        else:
            def record_llm_split(
                result,
                attempt_index: int,
                input_chars: int,
                input_tokens: int,
                pass_name: str,
            ) -> None:
                if llm_split_profile_path is None and not llm_split_console:
                    return
                output_line_count = len(result.lines or [])
                row = LlmSplitProfile(
                    chain_index=chain_index,
                    attempt_index=attempt_index,
                    input_chars=input_chars,
                    input_tokens=input_tokens,
                    max_chars=max_chars,
                    raw_line_count=result.raw_line_count,
                    clean_line_count=result.clean_line_count,
                    accepted=result.accepted,
                    reject_reason=result.reject_reason,
                    output_line_count=output_line_count,
                    pass_name=pass_name,
                    partial_accept_count=result.partial_accept_count,
                    partial_reject_count=result.partial_reject_count,
                    accepted_prefix_chars=result.accepted_prefix_chars,
                    remaining_chars_after_partial=len(result.remaining_text_after_partial),
                    input_preview=result.input_text[:120].replace("\r", " ").replace("\n", " "),
                    sentence_break_count=result.sentence_break_count,
                    connective_break_count=result.connective_break_count,
                )
                if llm_split_profile_path is not None:
                    llm_split_rows.append(row)
                    if not row.accepted or row.reject_reason == "partial_accept":
                        llm_split_rejections.append(
                            (
                                row,
                                result.input_text,
                                result.raw_response,
                                result.cleaned_lines,
                                result.partial_lines,
                                result.partial_rejected_lines,
                            )
                        )
                if llm_split_console:
                    status = "accepted" if row.accepted else "rejected"
                    detail = f"lines={row.output_line_count}" if row.accepted else row.reject_reason
                    print(
                        f"LLM split planning chain {chain_index} attempt {attempt_index}: {status}, {detail}",
                        flush=True,
                    )

            split_parts = split_token_chain(
                group.tokens,
                max_chars=max_chars,
                max_duration=max_duration,
                fallback=False,
                llm_splitter=llm_splitter,
                llm_split_callback=record_llm_split,
                llm_max_input_chars=llm_max_input_chars,
                llm_second_pass_max_input_chars=llm_second_pass_max_input_chars,
            )
        for part_index, sub in enumerate(split_parts):
            sub.chain_index = chain_index
            sub.chain_part_index = part_index
        subtitles.extend(split_parts)
        if regroup_profile_path is not None:
            start = chain.chunks[0].chunk.start
            end = chain.chunks[-1].chunk.end
            gaps = [
                chain.chunks[i].chunk.start - chain.chunks[i - 1].chunk.end
                for i in range(1, len(chain.chunks))
            ]
            regroup_rows.append(
                RegroupProfile(
                    chain_index=chain_index,
                    source_chunk_indexes=";".join(str(chunk.chunk.index) for chunk in chain.chunks),
                    start=start,
                    end=end,
                    duration_sec=end - start,
                    chunk_count=len(chain.chunks),
                    token_count=sum(len(chunk.tokens) for chunk in chain.chunks),
                    fallback=chain.fallback,
                    split_count=len(split_parts),
                    reason_closed=chain.reason_closed,
                    max_internal_chunk_gap=max(gaps, default=0.0),
                    avg_internal_chunk_gap=sum(gaps) / len(gaps) if gaps else 0.0,
                    gap_sec_used=chain.gap_sec,
                )
            )
    subtitles = [s for s in subtitles if s.text.strip()]
    subtitles.sort(key=lambda s: (s.start_time, s.end_time))
    left_merge_count = _left_merge_adjacent_subtitles(subtitles, max_chars)

    if regroup_profile_path is not None:
        write_regroup_profile(regroup_profile_path, regroup_rows)
        merged_chains = sum(1 for row in regroup_rows if row.chunk_count > 1)
        longest = max(regroup_rows, key=lambda row: row.duration_sec, default=None)
        print("Regroup diagnostics:", flush=True)
        print(f"  aligned_chunks={len(chunks)}", flush=True)
        print(f"  fallback_chunks={fallback_chunks}", flush=True)
        print(f"  tokenless_chunks={tokenless_chunks}", flush=True)
        print(f"  chain_count={len(regroup_rows)}", flush=True)
        print(f"  merged_chains={merged_chains}", flush=True)
        print(f"  left_merged_subtitles={left_merge_count}", flush=True)
        if longest is not None:
            print(f"  longest_chain_chunks={longest.chunk_count}", flush=True)
            print(f"  longest_chain_sec={longest.duration_sec:.2f}", flush=True)
        print(f"  subtitles_before_cleanup={len(subtitles)}", flush=True)
    if llm_split_profile_path is not None:
        write_llm_split_profile(llm_split_profile_path, llm_split_rows)
        print(f"Wrote LLM split diagnostics: {llm_split_profile_path}", flush=True)
        rejection_path = llm_split_profile_path.with_name(
            f"{llm_split_profile_path.stem}.rejected.txt"
        )
        if llm_split_rejections:
            write_llm_split_rejections(rejection_path, llm_split_rejections)
            print(f"Wrote rejected LLM split details: {rejection_path}", flush=True)

    for sub in subtitles:
        if sub.end_time <= sub.start_time:
            sub.end_time = sub.start_time + min_duration
            sub.timing_adjustment = _append_adjustment(sub.timing_adjustment, "min_duration")
        if sub.end_time - sub.start_time < min_duration:
            sub.end_time = sub.start_time + min_duration
            sub.timing_adjustment = _append_adjustment(sub.timing_adjustment, "min_duration")
        if sub.end_time - sub.start_time > max_duration:
            sub.end_time = sub.start_time + max_duration
            sub.timing_adjustment = _append_adjustment(sub.timing_adjustment, "max_duration")

    _touch_same_chain_subtitles(
        subtitles,
        chain_lead_in_sec,
        chain_lead_in_growth_sec,
        chain_lead_in_max_sec,
    )

    for i in range(len(subtitles) - 1):
        current = subtitles[i]
        nxt = subtitles[i + 1]
        if current.end_time > nxt.start_time:
            current.end_time = max(current.start_time + min_duration, nxt.start_time)
            current.timing_adjustment = _append_adjustment(current.timing_adjustment, "overlap_clamp")
        elif nxt.start_time - current.end_time <= gap_threshold:
            current.end_time = nxt.start_time
            current.timing_adjustment = _append_adjustment(current.timing_adjustment, "gap_threshold")

    _touch_same_chain_subtitles(
        subtitles,
        chain_lead_in_sec,
        chain_lead_in_growth_sec,
        chain_lead_in_max_sec,
        boundary_rows,
    )

    if subtitle_timing_profile_path is not None:
        _write_subtitle_timing_profile(subtitle_timing_profile_path, subtitles)
    if boundary_timing_profile_path is not None:
        write_boundary_timing_profile(boundary_timing_profile_path, boundary_rows)
    if chain_markers is not None:
        chain_markers.clear()
        chain_markers.extend(_build_chain_markers_from_subtitles(subtitles))

    if refiner is not None:
        _refine_subtitle_text(subtitles, refiner, max(1, cleanup_window_subtitles))
    return subtitles


def _append_adjustment(current: str, adjustment: str) -> str:
    if not current or current == "none":
        return adjustment
    if adjustment in current.split(";"):
        return current
    return f"{current};{adjustment}"


def _left_merge_adjacent_subtitles(subtitles: list[Subtitle], max_chars: int) -> int:
    merged: list[Subtitle] = []
    merge_count = 0
    for sub in subtitles:
        if not sub.text.strip():
            continue
        if merged and len(_normalized_text(merged[-1].text + sub.text)) <= max_chars:
            _merge_subtitle_left(merged[-1], sub)
            merge_count += 1
            continue
        merged.append(sub)
    if merge_count:
        subtitles[:] = merged
        _renumber_chain_parts(subtitles)
    return merge_count


def _merge_subtitle_left(left: Subtitle, right: Subtitle) -> None:
    left.end_time = max(left.end_time, right.end_time)
    left.text = f"{left.text}{right.text}"
    left.tokens.extend(right.tokens)
    left.alignment_fallback = left.alignment_fallback or right.alignment_fallback
    left.split_source = _merge_source_labels(left.split_source, right.split_source)
    left.timing_adjustment = _append_adjustment(left.timing_adjustment, "left_merge")


def _merge_source_labels(left: str, right: str) -> str:
    labels = []
    for label in (left, right):
        if label and label not in labels:
            labels.append(label)
    return "+".join(labels)


def _renumber_chain_parts(subtitles: list[Subtitle]) -> None:
    counts: dict[int, int] = {}
    for sub in subtitles:
        if sub.chain_index is None:
            continue
        part_index = counts.get(sub.chain_index, 0)
        sub.chain_part_index = part_index
        counts[sub.chain_index] = part_index + 1


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _touch_same_chain_subtitles(
    subtitles: list[Subtitle],
    lead_in_sec: float,
    lead_in_growth_sec: float,
    lead_in_max_sec: float,
    boundary_rows: list[BoundaryTimingProfile] | None = None,
) -> None:
    for i in range(len(subtitles) - 1):
        current = subtitles[i]
        nxt = subtitles[i + 1]
        if current.chain_index is None or current.chain_index != nxt.chain_index:
            continue
        original_next_start = nxt.start_time
        previous_token = current.tokens[-1] if current.tokens else None
        next_token = nxt.tokens[0] if nxt.tokens else None
        part_index = nxt.chain_part_index or 0
        effective_lead_in = min(
            max(lead_in_sec, 0.0) + max(lead_in_growth_sec, 0.0) * max(part_index, 0),
            max(lead_in_max_sec, lead_in_sec, 0.0),
        )
        boundary = previous_token.end if previous_token is not None else current.end_time
        if next_token is not None and effective_lead_in > 0:
            boundary = min(boundary, max(current.start_time, next_token.start - effective_lead_in))
        if boundary <= current.start_time:
            boundary = nxt.start_time
        current.end_time = boundary
        if boundary < nxt.start_time:
            nxt.start_time = boundary
            nxt.timing_adjustment = _append_adjustment(nxt.timing_adjustment, "chain_boundary_pull")
            if next_token is not None and effective_lead_in > 0:
                nxt.timing_adjustment = _append_adjustment(nxt.timing_adjustment, "chain_lead_in")
        current.timing_adjustment = _append_adjustment(current.timing_adjustment, "chain_touch")
        if boundary_rows is not None:
            boundary_rows.append(
                BoundaryTimingProfile(
                    subtitle_index=i + 1,
                    chain_index=nxt.chain_index if nxt.chain_index is not None else "",
                    previous_text_tail=current.text[-24:],
                    next_text_head=nxt.text[:24],
                    previous_token_text=previous_token.text if previous_token is not None else "",
                    previous_token_start=previous_token.start if previous_token is not None else "",
                    previous_token_end=previous_token.end if previous_token is not None else "",
                    next_token_text=next_token.text if next_token is not None else "",
                    next_token_start=next_token.start if next_token is not None else "",
                    next_token_end=next_token.end if next_token is not None else "",
                    original_next_start=original_next_start,
                    adjusted_next_start=nxt.start_time,
                    pull_sec=original_next_start - nxt.start_time,
                    lead_in_sec=effective_lead_in,
                    boundary=boundary,
                )
            )


def _write_subtitle_timing_profile(path: Path, subtitles: list[Subtitle]) -> None:
    rows: list[SubtitleTimingProfile] = []
    previous: Subtitle | None = None
    for index, sub in enumerate(subtitles):
        first_token_start = sub.tokens[0].start if sub.tokens else ""
        last_token_end = sub.tokens[-1].end if sub.tokens else ""
        prev_end = previous.end_time if previous is not None else ""
        gap = sub.start_time - previous.end_time if previous is not None else ""
        same_chain = (
            previous is not None
            and previous.chain_index is not None
            and previous.chain_index == sub.chain_index
        )
        rows.append(
            SubtitleTimingProfile(
                subtitle_index=index,
                chain_index=sub.chain_index if sub.chain_index is not None else "",
                chain_part_index=sub.chain_part_index if sub.chain_part_index is not None else "",
                start=sub.start_time,
                end=sub.end_time,
                duration=sub.end_time - sub.start_time,
                text_chars=len(sub.text),
                token_count=len(sub.tokens),
                first_token_start=first_token_start,
                last_token_end=last_token_end,
                prev_end=prev_end,
                gap_from_prev=gap,
                same_chain_as_prev=same_chain,
                source=sub.split_source,
                timing_adjustment=sub.timing_adjustment,
            )
        )
        previous = sub
    write_subtitle_timing_profile(path, rows)


def _build_chain_markers_from_subtitles(subtitles: list[Subtitle]) -> list[ExoMarker]:
    markers: list[ExoMarker] = []
    by_chain: dict[int, list[Subtitle]] = {}
    for sub in subtitles:
        if sub.chain_index is None:
            continue
        by_chain.setdefault(sub.chain_index, []).append(sub)
    for chain_subtitles in by_chain.values():
        if len(chain_subtitles) <= 1:
            continue
        ordered = sorted(chain_subtitles, key=lambda item: (item.start_time, item.end_time))
        markers.append(ExoMarker(ordered[0].start_time, ordered[-1].end_time))
    return markers


def _refine_subtitle_text(subtitles: list[Subtitle], refiner: TextRefiner, window_size: int) -> None:
    i = 0
    total = len(subtitles)
    while i < len(subtitles):
        window = subtitles[i : i + window_size]
        print(f"Cleaning subtitles {i + 1}-{i + len(window)}/{total}...", flush=True)
        original = [sub.text for sub in window]
        refined = refiner.refine(original)
        if len(refined) == len(window):
            for sub, text in zip(window, refined):
                if text.strip():
                    sub.text = text.strip()
        i += window_size
