"""Alignment-aware subtitle grouping and cleanup."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .models import AlignedChunk, AlignedToken, Subtitle
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
from .splitter import SENTENCE_TERMINAL_SOURCE, split_aligned_chunk, split_token_chain
from .text_refiner import TextRefiner

BOUNDARY_REVIEW_TERMS = (
    "について",
    "という",
    "でも",
    "けど",
    "ので",
    "から",
    "って",
    "も",
    "で",
    "が",
)
BOUNDARY_REVIEW_PUNCTUATION = set("、,;:")


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
    gap_sec: float,
    ramp_start_sec: float = 0.2,
    ramp_step_sec: float = 0.1,
    ramp_max_chain_sec: float = 120.0,
    ramp_max_chain_tokens: int = 900,
) -> list[Chain]:
    ordered = sorted(chunks, key=lambda item: (item.chunk.start, item.chunk.end))
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
    regroup_gap_sec: float,
    refiner: TextRefiner | None = None,
    llm_splitter: TextRefiner | None = None,
    regroup_profile_path: Path | None = None,
    llm_split_profile_path: Path | None = None,
    llm_split_console: bool = False,
    subtitle_timing_profile_path: Path | None = None,
    boundary_timing_profile_path: Path | None = None,
    cleanup_diff_path: Path | None = None,
    chain_lead_in_sec: float = 0.20,
    cleanup_window_subtitles: int = 1,
    cleanup_workers: int = 1,
    chain_split_workers: int = 1,
) -> list[Subtitle]:
    chains = group_aligned_chains(
        chunks,
        gap_sec=regroup_gap_sec,
    )
    subtitles: list[Subtitle] = []
    regroup_rows: list[RegroupProfile] = []
    llm_split_rows: list[LlmSplitProfile] = []
    llm_split_rejections: list[tuple[LlmSplitProfile, str, str, list[str]]] = []
    boundary_rows: list[BoundaryTimingProfile] = []
    fallback_chunks = 0
    tokenless_chunks = 0
    def split_chain(chain_index: int, chain: Chain):
        local_llm_rows: list[LlmSplitProfile] = []
        local_llm_rejections: list[tuple[LlmSplitProfile, str, str, list[str], list[str], list[str]]] = []
        local_fallback = 0
        local_tokenless = 0
        group = _merge_window(chain.chunks) if len(chain.chunks) > 1 and not chain.fallback else chain.chunks[0]
        if group.fallback:
            local_fallback += 1
            split_parts = split_aligned_chunk(group, max_chars)
        elif not group.tokens:
            local_tokenless += 1
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
                    local_llm_rows.append(row)
                    if not row.accepted or row.reject_reason == "partial_accept":
                        local_llm_rejections.append(
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
            )
        for part_index, sub in enumerate(split_parts):
            sub.chain_index = chain_index
            sub.chain_part_index = part_index
            sub.cleanup_group_index = _cleanup_group_for_subtitle(sub, chain)
        start = chain.chunks[0].chunk.start
        end = chain.chunks[-1].chunk.end
        gaps = [
            chain.chunks[i].chunk.start - chain.chunks[i - 1].chunk.end
            for i in range(1, len(chain.chunks))
        ]
        regroup_row = RegroupProfile(
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
        return chain_index, split_parts, regroup_row, local_llm_rows, local_llm_rejections, local_fallback, local_tokenless

    if chain_split_workers > 1 and len(chains) > 1:
        split_results = []
        with ThreadPoolExecutor(max_workers=max(1, chain_split_workers)) as pool:
            futures = [pool.submit(split_chain, chain_index, chain) for chain_index, chain in enumerate(chains)]
            for future in as_completed(futures):
                split_results.append(future.result())
    else:
        split_results = [split_chain(chain_index, chain) for chain_index, chain in enumerate(chains)]

    for _, split_parts, regroup_row, local_rows, local_rejections, local_fallback, local_tokenless in sorted(
        split_results,
        key=lambda item: item[0],
    ):
        subtitles.extend(split_parts)
        fallback_chunks += local_fallback
        tokenless_chunks += local_tokenless
        llm_split_rows.extend(local_rows)
        llm_split_rejections.extend(local_rejections)
        if regroup_profile_path is not None:
            regroup_rows.append(regroup_row)
    subtitles = [s for s in subtitles if s.text.strip()]
    subtitles.sort(key=lambda s: (s.start_time, s.end_time))
    left_merge_count = _left_merge_adjacent_subtitles(subtitles, max_chars)
    stripped_period_count = _strip_standard_sentence_periods(subtitles)
    boundary_review_count = 0
    if refiner is not None:
        boundary_review_count = _review_same_chain_leading_phrases(subtitles, refiner, max_chars)

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
        print(f"  stripped_sentence_periods={stripped_period_count}", flush=True)
        print(f"  boundary_review_moves={boundary_review_count}", flush=True)
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
        0.0,
        max(0.20, chain_lead_in_sec),
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
        0.0,
        max(0.20, chain_lead_in_sec),
        boundary_rows,
    )

    if subtitle_timing_profile_path is not None:
        _write_subtitle_timing_profile(subtitle_timing_profile_path, subtitles)
    if boundary_timing_profile_path is not None:
        write_boundary_timing_profile(boundary_timing_profile_path, boundary_rows)

    if refiner is not None:
        _refine_subtitle_text(
            subtitles,
            refiner,
            max(1, cleanup_window_subtitles),
            max(1, cleanup_workers),
            cleanup_diff_path,
        )
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
        if (
            merged
            and _same_chain_for_left_merge(merged[-1], sub)
            and not _blocks_following_left_merge(merged[-1])
            and len(_normalized_text(merged[-1].text + sub.text)) <= max_chars
        ):
            _merge_subtitle_left(merged[-1], sub)
            merge_count += 1
            continue
        merged.append(sub)
    if merge_count:
        subtitles[:] = merged
        _renumber_chain_parts(subtitles)
    return merge_count


def _same_chain_for_left_merge(left: Subtitle, right: Subtitle) -> bool:
    if left.chain_index is None or right.chain_index is None:
        return left.chain_index is right.chain_index
    return left.chain_index == right.chain_index


def _blocks_following_left_merge(subtitle: Subtitle) -> bool:
    return SENTENCE_TERMINAL_SOURCE in subtitle.split_source.split("+")


def _merge_subtitle_left(left: Subtitle, right: Subtitle) -> None:
    left.end_time = max(left.end_time, right.end_time)
    left.text = f"{left.text}{right.text}"
    left.tokens.extend(right.tokens)
    left.alignment_fallback = left.alignment_fallback or right.alignment_fallback
    left.split_source = _merge_source_labels(left.split_source, right.split_source)
    left.timing_adjustment = _append_adjustment(left.timing_adjustment, "left_merge")


def _strip_standard_sentence_periods(subtitles: list[Subtitle]) -> int:
    stripped = 0
    kept: list[Subtitle] = []
    for sub in subtitles:
        if not sub.text.rstrip().endswith("。"):
            kept.append(sub)
            continue
        stripped += 1
        while sub.tokens and sub.tokens[-1].text.rstrip() == "。":
            sub.tokens.pop()
        if sub.tokens and sub.tokens[-1].text.endswith("。"):
            sub.tokens[-1].text = sub.tokens[-1].text.rstrip("。")
        if sub.tokens:
            _refresh_subtitle_from_tokens(sub)
        else:
            sub.text = sub.text.rstrip().rstrip("。")
        sub.timing_adjustment = _append_adjustment(sub.timing_adjustment, "strip_sentence_period")
        if sub.text.strip():
            kept.append(sub)
    if len(kept) != len(subtitles):
        subtitles[:] = kept
        _renumber_chain_parts(subtitles)
    return stripped


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


def _leading_boundary_phrase_len(tokens: list[AlignedToken]) -> int:
    if not tokens:
        return 0
    text = _normalized_text(_tokens_to_text(tokens))
    for term in sorted(BOUNDARY_REVIEW_TERMS, key=len, reverse=True):
        for punctuation in BOUNDARY_REVIEW_PUNCTUATION:
            phrase = f"{term}{punctuation}"
            if not text.startswith(phrase):
                continue
            seen = ""
            for index, token in enumerate(tokens, start=1):
                seen += _normalized_text(token.text)
                if len(seen) >= len(phrase):
                    return index if seen == phrase else 0
    return 0


def _refresh_subtitle_from_tokens(sub: Subtitle) -> None:
    if not sub.tokens:
        return
    sub.text = _tokens_to_text(sub.tokens)
    sub.start_time = sub.tokens[0].start
    sub.end_time = sub.tokens[-1].end


def _move_leading_phrase_left(left: Subtitle, right: Subtitle, token_count: int) -> None:
    moved = right.tokens[:token_count]
    remaining = right.tokens[token_count:]
    if not moved or not remaining:
        return
    left.tokens.extend(moved)
    right.tokens = remaining
    _refresh_subtitle_from_tokens(left)
    _refresh_subtitle_from_tokens(right)
    left.split_source = _merge_source_labels(left.split_source, "llm_boundary_review")
    right.split_source = _merge_source_labels(right.split_source, "llm_boundary_review")
    left.timing_adjustment = _append_adjustment(left.timing_adjustment, "llm_boundary_review")
    right.timing_adjustment = _append_adjustment(right.timing_adjustment, "llm_boundary_review")


def _review_same_chain_leading_phrases(
    subtitles: list[Subtitle],
    refiner: TextRefiner,
    max_chars: int,
) -> int:
    moved_count = 0
    for index in range(1, len(subtitles)):
        previous = subtitles[index - 1]
        current = subtitles[index]
        if previous.chain_index is None or previous.chain_index != current.chain_index:
            continue
        phrase_tokens = _leading_boundary_phrase_len(current.tokens)
        if phrase_tokens <= 0:
            continue
        phrase_text = _tokens_to_text(current.tokens[:phrase_tokens])
        if len(_normalized_text(previous.text + phrase_text)) > max_chars:
            continue
        if not refiner.should_move_leading_phrase_left(previous.text, current.text, phrase_text):
            continue
        _move_leading_phrase_left(previous, current, phrase_tokens)
        moved_count += 1
    if moved_count:
        _renumber_chain_parts(subtitles)
        print(f"LLM boundary phrase review moved {moved_count} leading phrase(s).", flush=True)
    return moved_count


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


def _cleanup_group_for_subtitle(subtitle: Subtitle, chain: Chain) -> int | None:
    if not chain.chunks:
        return subtitle.chain_index
    probe_time = subtitle.start_time
    if subtitle.tokens:
        first = subtitle.tokens[0]
        probe_time = (first.start + first.end) / 2
    for chunk in chain.chunks:
        if chunk.chunk.start <= probe_time <= chunk.chunk.end:
            return chunk.chunk.vad_group_index if chunk.chunk.vad_group_index is not None else chunk.chunk.index
    first = chain.chunks[0].chunk
    return first.vad_group_index if first.vad_group_index is not None else first.index


def _cleanup_group_key(subtitle: Subtitle) -> int | None:
    return subtitle.cleanup_group_index if subtitle.cleanup_group_index is not None else subtitle.chain_index


def _cleanup_windows(subtitles: list[Subtitle], window_size: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    i = 0
    while i < len(subtitles):
        group_index = _cleanup_group_key(subtitles[i])
        end = i + 1
        while (
            end < len(subtitles)
            and end - i < window_size
            and _cleanup_group_key(subtitles[end]) == group_index
        ):
            end += 1
        windows.append((i, end))
        i = end
    return windows


def _refine_window(refiner: TextRefiner, subtitles: list[Subtitle], start: int, end: int) -> tuple[int, list[str]]:
    original = [sub.text for sub in subtitles[start:end]]
    return start, refiner.refine(original)


def _record_cleanup_change(changes: list[tuple[int, str, str]], index: int, before: str, after: str) -> None:
    if before == after:
        return
    changes.append((index, before, after))


def _write_cleanup_diff(path: Path, changes: list[tuple[int, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, before, after in sorted(changes, key=lambda item: item[0]):
        rank, reason = _cleanup_change_rank(before, after)
        lines.extend([f"{index}.", f"rank:   {rank} ({reason})", f"before: {before}", f"after:  {after}", ""])
    text = "\n".join(lines).rstrip()
    path.write_text((text + "\n") if text else "NO CHANGES\n", encoding="utf-8")


def _cleanup_change_rank(before: str, after: str) -> tuple[str, str]:
    before_compact = _normalized_text(before)
    after_compact = _normalized_text(after)
    removed = before_compact
    for filler in ("えー", "え", "あー", "あ", "うーん", "あの", "その", "まあ", "ま"):
        removed = removed.replace(filler, "")
    punctuation_normalized = re.sub(r"[、。,.，．]", "", before_compact) == re.sub(r"[、。,.，．]", "", after_compact)
    if after_compact == removed or punctuation_normalized:
        return "low", "filler/punctuation cleanup"
    delta = abs(len(after_compact) - len(before_compact))
    if delta <= max(4, len(before_compact) // 5):
        return "medium", "small wording change"
    return "high", "large text change"


def _refine_subtitle_text(
    subtitles: list[Subtitle],
    refiner: TextRefiner,
    window_size: int,
    workers: int = 1,
    cleanup_diff_path: Path | None = None,
) -> None:
    total = len(subtitles)
    windows = _cleanup_windows(subtitles, window_size)
    changes: list[tuple[int, str, str]] = []
    if workers > 1 and len(windows) > 1:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {}
            for start, end in windows:
                print(f"Cleaning subtitles {start + 1}-{end}/{total}...", flush=True)
                futures[pool.submit(_refine_window, refiner, subtitles, start, end)] = (start, end)
            for future in as_completed(futures):
                start, end = futures[future]
                _, refined = future.result()
                window = subtitles[start:end]
                if len(refined) == len(window):
                    for offset, (sub, text) in enumerate(zip(window, refined), start=start + 1):
                        if text.strip():
                            _record_cleanup_change(changes, offset, sub.text, text.strip())
                            sub.text = text.strip()
        if cleanup_diff_path is not None:
            _write_cleanup_diff(cleanup_diff_path, changes)
        return

    for start, end in windows:
        window = subtitles[start:end]
        print(f"Cleaning subtitles {start + 1}-{end}/{total}...", flush=True)
        refined = refiner.refine([sub.text for sub in window])
        if len(refined) == len(window):
            for offset, (sub, text) in enumerate(zip(window, refined), start=start + 1):
                if text.strip():
                    _record_cleanup_change(changes, offset, sub.text, text.strip())
                    sub.text = text.strip()
    if cleanup_diff_path is not None:
        _write_cleanup_diff(cleanup_diff_path, changes)
