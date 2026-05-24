"""Subtitle shaping and splitting."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .models import AlignedChunk, AlignedToken, SplitPlanResult, Subtitle

SENTENCE_BREAK_CHARS = set("。！？!?")
PHRASE_BREAK_CHARS = set("、,;:")
JAPANESE_CONNECTIVES = ("について", "という", "けど", "ので", "から", "って", "で", "が")


@dataclass
class TokenSegment:
    tokens: list[AlignedToken]
    source: str


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _min_split_chars(max_chars: int) -> int:
    return max(6, max_chars // 4)


def _normalized_len(tokens: list[AlignedToken]) -> int:
    return len(_normalized(_tokens_to_text(tokens)))


def _best_cut(text: str, max_chars: int) -> int:
    search_limit = min(len(text), max_chars)
    for i in range(search_limit - 1, max(max_chars // 3, 1) - 1, -1):
        if text[i] in SENTENCE_BREAK_CHARS:
            return i + 1
    return search_limit


def _punctuation_cut(text: str, max_chars: int) -> int | None:
    search_limit = min(len(text), max_chars)
    for i in range(search_limit - 1, max(max_chars // 3, 1) - 1, -1):
        if text[i] in SENTENCE_BREAK_CHARS:
            return i + 1
    return None


def _tokens_to_text(tokens: list[AlignedToken]) -> str:
    if not tokens:
        return ""
    if all(t.kind == "char" for t in tokens):
        return "".join(t.text for t in tokens)
    return "".join(t.text for t in tokens)


def _is_safe_boundary(tokens: list[AlignedToken], index: int) -> bool:
    if index <= 0 or index >= len(tokens):
        return False
    left = tokens[index - 1].text
    right = tokens[index].text
    if left == "." and right and right[0].isdigit():
        return False
    if left and right and left[-1].isdigit() and right[0] == ".":
        return False
    if left.isascii() and right.isascii() and left[-1:].isalnum() and right[:1].isalnum():
        return False
    return True


def _boundary_score(tokens: list[AlignedToken], index: int, target_chars: int) -> tuple[int, int] | None:
    if not _is_safe_boundary(tokens, index):
        return None
    text_before = _tokens_to_text(tokens[:index])
    norm_len = len(_normalized(text_before))
    distance = abs(norm_len - target_chars)
    previous_text = tokens[index - 1].text
    if previous_text and previous_text[-1] in SENTENCE_BREAK_CHARS:
        return (0, distance)
    text_for_match = text_before
    if text_for_match and text_for_match[-1] in PHRASE_BREAK_CHARS:
        text_for_match = text_for_match[:-1]
    if any(text_for_match.endswith(term) for term in JAPANESE_CONNECTIVES):
        return (1, distance)
    return None


def _first_sentence_cut(tokens: list[AlignedToken], max_chars: int) -> int | None:
    for index in range(1, len(tokens)):
        if not _is_safe_boundary(tokens, index):
            continue
        if not _cut_is_balanced_enough(tokens, index, max_chars):
            continue
        previous_text = tokens[index - 1].text
        if previous_text and previous_text[-1] in SENTENCE_BREAK_CHARS:
            return index
    return None


def _connective_cut(tokens: list[AlignedToken], max_chars: int) -> int | None:
    if len(tokens) <= 1:
        return None
    candidates: list[tuple[int, int]] = []
    min_chars = max(1, max_chars // 3)
    for index in range(1, len(tokens)):
        if not _is_safe_boundary(tokens, index):
            continue
        chars = len(_normalized(_tokens_to_text(tokens[:index])))
        if chars < min_chars:
            continue
        if not _cut_is_balanced_enough(tokens, index, max_chars):
            continue
        text_before = _tokens_to_text(tokens[:index])
        text_for_match = text_before
        if text_for_match and text_for_match[-1] in PHRASE_BREAK_CHARS:
            text_for_match = text_for_match[:-1]
        if any(text_for_match.endswith(term) for term in JAPANESE_CONNECTIVES):
            candidates.append((abs(chars - max_chars), index))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _best_structural_cut(tokens: list[AlignedToken], max_chars: int) -> int | None:
    if len(tokens) <= 1:
        return None
    candidates: list[tuple[tuple[int, int], int]] = []
    search_limit = min(len(tokens), max_chars + max(6, max_chars // 3))
    min_chars = max(1, max_chars // 3)
    for index in range(1, search_limit):
        chars = len(_normalized(_tokens_to_text(tokens[:index])))
        if chars < min_chars:
            continue
        score = _boundary_score(tokens, index, max_chars)
        if score is not None:
            candidates.append((score, index))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _map_lines_to_token_counts(tokens: list[AlignedToken], lines: list[str]) -> list[int] | None:
    counts: list[int] = []
    cursor = 0
    for line in lines:
        target = len(_normalized(line))
        if target <= 0:
            return None
        seen = 0
        start = cursor
        while cursor < len(tokens) and seen < target:
            seen += len(_normalized(tokens[cursor].text))
            cursor += 1
        if seen != target or cursor == start:
            return None
        counts.append(cursor - start)
    if cursor != len(tokens):
        return None
    return counts


def _map_prefix_lines_to_token_groups(
    tokens: list[AlignedToken],
    lines: list[str],
    max_chars: int,
    enforce_max_chars: bool = True,
) -> tuple[list[list[AlignedToken]], list[AlignedToken], str | None, list[str], list[str]]:
    groups: list[list[AlignedToken]] = []
    cursor = 0
    accepted: list[str] = []
    rejected: list[str] = []
    for line_index, line in enumerate(lines):
        norm_line = _normalized(line)
        if not norm_line:
            rejected = lines[line_index:]
            return groups, tokens[cursor:], "invalid_line", accepted, rejected
        if enforce_max_chars and len(norm_line) > max_chars:
            rejected = lines[line_index:]
            return groups, tokens[cursor:], "line_over_max_chars", accepted, rejected
        seen = ""
        start = cursor
        while cursor < len(tokens) and len(seen) < len(norm_line):
            seen += _normalized(tokens[cursor].text)
            cursor += 1
        if seen != norm_line or cursor == start:
            rejected = lines[line_index:]
            reason = "line_order_mismatch" if groups else "line_not_substring"
            return groups, tokens[start:], reason, accepted, rejected
        groups.append(tokens[start:cursor])
        accepted.append(line)
    return groups, tokens[cursor:], None, accepted, rejected


def _subtitles_from_token_groups(groups: list[list[AlignedToken]], fallback: bool, source: str) -> list[Subtitle]:
    subtitles = []
    for group in groups:
        if not group:
            continue
        subtitles.append(
            Subtitle(
                start_time=group[0].start,
                end_time=group[-1].end,
                text=_tokens_to_text(group),
                tokens=group,
                alignment_fallback=fallback,
                split_source=source,
            )
        )
    return subtitles


def _segments_to_subtitles(segments: list[TokenSegment], fallback: bool) -> list[Subtitle]:
    subtitles = []
    for segment in segments:
        subtitles.extend(_subtitles_from_token_groups([segment.tokens], fallback, segment.source))
    return subtitles


def _split_once_at(tokens: list[AlignedToken], index: int, source: str) -> list[TokenSegment]:
    index = max(1, min(index, len(tokens) - 1))
    return [TokenSegment(tokens[:index], source), TokenSegment(tokens[index:], source)]


def _cut_is_balanced_enough(tokens: list[AlignedToken], index: int, max_chars: int) -> bool:
    if index <= 0 or index >= len(tokens):
        return False
    minimum = _min_split_chars(max_chars)
    return _normalized_len(tokens[:index]) >= minimum and _normalized_len(tokens[index:]) >= minimum


def _over_limit(segment: TokenSegment, max_chars: int) -> bool:
    return len(_normalized(_tokens_to_text(segment.tokens))) > max_chars


def _split_pass1(segment: TokenSegment, max_chars: int) -> list[TokenSegment]:
    pending = [segment]
    changed = True
    for mode in ("sentence", "connective"):
        changed = True
        while changed:
            changed = False
            next_pending: list[TokenSegment] = []
            for item in pending:
                if not _over_limit(item, max_chars):
                    next_pending.append(item)
                    continue
                cut = _first_sentence_cut(item.tokens, max_chars) if mode == "sentence" else _connective_cut(item.tokens, max_chars)
                if cut is None:
                    next_pending.append(item)
                    continue
                source = "sentence_pass1" if mode == "sentence" else "connective_pass1"
                next_pending.extend(_split_once_at(item.tokens, cut, source))
                changed = True
            pending = next_pending
    return pending


def _phrase_cut(tokens: list[AlignedToken], target_chars: int, mode: str, max_chars: int) -> int | None:
    candidates: list[tuple[int, int]] = []
    total_chars = len(_normalized(_tokens_to_text(tokens)))
    if mode == "center":
        target_chars = max(1, total_chars // 2)
    for index in range(1, len(tokens)):
        if not _is_safe_boundary(tokens, index):
            continue
        previous_text = tokens[index - 1].text
        if not previous_text or previous_text[-1] not in PHRASE_BREAK_CHARS:
            continue
        if not _cut_is_balanced_enough(tokens, index, max_chars):
            continue
        chars = len(_normalized(_tokens_to_text(tokens[:index])))
        candidates.append((abs(chars - target_chars), index))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _phrase_split_until(
    segment: TokenSegment,
    max_chars: int,
    target_chars: int,
    source: str,
    mode: str,
    stop_at_chars: int | None = None,
) -> list[TokenSegment]:
    pending = [segment]
    changed = True
    while changed:
        changed = False
        next_pending: list[TokenSegment] = []
        for item in pending:
            length = len(_normalized(_tokens_to_text(item.tokens)))
            if length <= max_chars or (stop_at_chars is not None and length <= stop_at_chars):
                next_pending.append(item)
                continue
            cut = _phrase_cut(item.tokens, target_chars, mode, max_chars)
            if cut is None:
                next_pending.append(item)
                continue
            next_pending.extend(_split_once_at(item.tokens, cut, source))
            changed = True
        pending = next_pending
    return pending


def _max_char_split(segment: TokenSegment, max_chars: int) -> list[TokenSegment]:
    remaining = segment.tokens[:]
    result: list[TokenSegment] = []
    minimum = _min_split_chars(max_chars)
    while remaining:
        if len(_normalized(_tokens_to_text(remaining))) <= max_chars:
            result.append(TokenSegment(remaining, "max_chars_pass7" if segment.source == "initial" else segment.source))
            break
        cut = None
        count = 0
        for index, token in enumerate(remaining, start=1):
            count += len(_normalized(token.text))
            if count >= max_chars:
                if _is_safe_boundary(remaining, index):
                    cut = index
                else:
                    cut = max(1, index - 1)
                break
        if cut is None:
            cut = min(max(1, max_chars), len(remaining) - 1)
        cut = max(1, min(cut, len(remaining)))
        tail_len = _normalized_len(remaining[cut:])
        if 0 < tail_len < minimum:
            target_left_len = max(minimum, _normalized_len(remaining) - minimum)
            adjusted_cut = None
            count = 0
            for index, token in enumerate(remaining, start=1):
                count += len(_normalized(token.text))
                if count >= target_left_len:
                    adjusted_cut = index if _is_safe_boundary(remaining, index) else max(1, index - 1)
                    break
            if adjusted_cut is not None and adjusted_cut > 0:
                cut = max(1, min(adjusted_cut, len(remaining) - 1))
        result.append(TokenSegment(remaining[:cut], "max_chars_pass7"))
        remaining = remaining[cut:]
    return result


def split_token_chain(
    tokens: list[AlignedToken],
    max_chars: int,
    max_duration: float,
    fallback: bool = False,
    llm_splitter=None,
    llm_split_callback: Callable[[SplitPlanResult, int, int, int], None] | None = None,
    llm_max_input_chars: int = 240,
    llm_second_pass_max_input_chars: int = 240,
) -> list[Subtitle]:
    attempt_index = 0
    segments = [TokenSegment(tokens[:], "initial")]

    def run_llm_pass(segment: TokenSegment, pass_name: str, input_limit: int) -> list[TokenSegment]:
        nonlocal attempt_index
        text = _tokens_to_text(segment.tokens)
        sentence_break_count = sum(1 for char in text if char in SENTENCE_BREAK_CHARS)
        connective_break_count = sum(text.count(term) for term in JAPANESE_CONNECTIVES)
        attempt_index += 1
        if len(_normalized(text)) > input_limit or llm_splitter is None:
            result = SplitPlanResult(
                lines=None,
                accepted=False,
                reject_reason="skipped_input_too_large" if llm_splitter is not None else "llm_unavailable",
                input_text=text,
                sentence_break_count=sentence_break_count,
                connective_break_count=connective_break_count,
            )
            if llm_split_callback is not None and llm_splitter is not None:
                llm_split_callback(result, attempt_index, len(_normalized(text)), len(segment.tokens), pass_name)
            return [segment]
        if hasattr(llm_splitter, "split_lines_with_diagnostics"):
            result = llm_splitter.split_lines_with_diagnostics(text, max_chars)
        else:
            lines = llm_splitter.split_lines(text, max_chars)
            result = SplitPlanResult(
                lines=lines,
                raw_line_count=len(lines or []),
                clean_line_count=len(lines or []),
                accepted=lines is not None,
                reject_reason="none" if lines is not None else "request_failed",
                input_text=text,
                cleaned_lines=lines or [],
            )
        result.sentence_break_count = sentence_break_count
        result.connective_break_count = connective_break_count
        lines = result.lines or result.cleaned_lines
        if lines:
            if len(lines) != 2:
                result = SplitPlanResult(
                    lines=None,
                    raw_line_count=result.raw_line_count,
                    clean_line_count=result.clean_line_count,
                    accepted=False,
                    reject_reason="wrong_line_count",
                    input_text=result.input_text,
                    raw_response=result.raw_response,
                    cleaned_lines=result.cleaned_lines,
                    sentence_break_count=result.sentence_break_count,
                    connective_break_count=result.connective_break_count,
                )
                if llm_split_callback is not None:
                    llm_split_callback(result, attempt_index, len(_normalized(text)), len(segment.tokens), pass_name)
                return [segment]
            groups, remainder, reason, accepted, rejected = _map_prefix_lines_to_token_groups(
                segment.tokens, lines, max_chars, enforce_max_chars=False
            )
            if reason is None and not remainder:
                result.lines = accepted
                result.accepted = True
                result.reject_reason = "none"
                result.partial_accept_count = len(accepted)
                if llm_split_callback is not None:
                    llm_split_callback(result, attempt_index, len(_normalized(text)), len(segment.tokens), pass_name)
                return [TokenSegment(group, pass_name) for group in groups]
            if groups:
                result.lines = accepted
                result.accepted = True
                result.reject_reason = "partial_accept"
                result.partial_lines = accepted
                result.partial_rejected_lines = rejected
                result.partial_accept_count = len(accepted)
                result.partial_reject_count = len(rejected)
                result.accepted_prefix_chars = len(_normalized("".join(accepted)))
                result.remaining_text_after_partial = _tokens_to_text(remainder)
                if llm_split_callback is not None:
                    llm_split_callback(result, attempt_index, len(_normalized(text)), len(segment.tokens), pass_name)
                return [TokenSegment(group, pass_name) for group in groups] + [TokenSegment(remainder, segment.source)]
            result = SplitPlanResult(
                lines=None,
                raw_line_count=result.raw_line_count,
                clean_line_count=result.clean_line_count,
                accepted=False,
                reject_reason=reason or "no_valid_prefix",
                input_text=result.input_text,
                raw_response=result.raw_response,
                cleaned_lines=result.cleaned_lines,
                partial_rejected_lines=rejected,
                partial_reject_count=len(rejected),
                sentence_break_count=result.sentence_break_count,
                connective_break_count=result.connective_break_count,
            )
        if llm_split_callback is not None:
            llm_split_callback(result, attempt_index, len(_normalized(text)), len(segment.tokens), pass_name)
        return [segment]

    # Pass 1: strong semantic structure.
    next_segments: list[TokenSegment] = []
    for segment in segments:
        next_segments.extend(_split_pass1(segment, max_chars))
    segments = next_segments

    # Pass 2: center phrase punctuation only for blocks too large for LLM.
    next_segments = []
    for segment in segments:
        next_segments.extend(
            _phrase_split_until(
                segment,
                max_chars=max_chars,
                target_chars=max_chars,
                source="phrase_center_pass2",
                mode="center",
                stop_at_chars=llm_max_input_chars,
            )
        )
    segments = next_segments

    # Pass 3: LLM split.
    next_segments = []
    for segment in segments:
        if _over_limit(segment, max_chars):
            next_segments.extend(run_llm_pass(segment, "llm_pass3", llm_max_input_chars))
        else:
            next_segments.append(segment)
    segments = next_segments

    # Pass 4: tighter phrase split.
    tight_target = max(12, int(max_chars * 0.75))
    next_segments = []
    for segment in segments:
        next_segments.extend(
            _phrase_split_until(
                segment,
                max_chars=max_chars,
                target_chars=tight_target,
                source="phrase_tight_pass4",
                mode="target",
            )
        )
    segments = next_segments

    # Pass 5: LLM split on shorter blocks.
    next_segments = []
    for segment in segments:
        if _over_limit(segment, max_chars):
            next_segments.extend(run_llm_pass(segment, "llm_pass5", llm_second_pass_max_input_chars))
        else:
            next_segments.append(segment)
    segments = next_segments

    # Pass 6: phrase punctuation near max limit.
    next_segments = []
    for segment in segments:
        next_segments.extend(
            _phrase_split_until(
                segment,
                max_chars=max_chars,
                target_chars=max_chars,
                source="phrase_limit_pass6",
                mode="target",
            )
        )
    segments = next_segments

    # Pass 7: hard max char compliance.
    next_segments = []
    for segment in segments:
        if _over_limit(segment, max_chars):
            next_segments.extend(_max_char_split(segment, max_chars))
        else:
            next_segments.append(segment)
    segments = next_segments

    return _segments_to_subtitles(segments, fallback)


def _llm_cut_index(
    tokens: list[AlignedToken],
    text: str,
    max_chars: int,
    split_callback: Callable[[str, int], tuple[str, str] | None] | None,
) -> int | None:
    if split_callback is None:
        return None
    suggestion = split_callback(text, max_chars)
    if suggestion is None:
        return None
    left, _right = suggestion
    target = _normalized(left)
    count = 0
    for i, token in enumerate(tokens):
        count += len(_normalized(token.text))
        if count >= len(target):
            return i + 1
    return None


def split_aligned_chunk(
    chunk: AlignedChunk,
    max_chars: int,
    split_callback: Callable[[str, int], tuple[str, str] | None] | None = None,
) -> list[Subtitle]:
    text = chunk.text.strip()
    if not text:
        return []
    if not chunk.tokens:
        return [Subtitle(chunk.chunk.start, chunk.chunk.end, text, [], chunk.fallback)]

    parts: list[Subtitle] = []
    remaining = chunk.tokens[:]
    while remaining:
        current_text = _tokens_to_text(remaining)
        if len(_normalized(current_text)) <= max_chars:
            parts.append(
                Subtitle(
                    start_time=remaining[0].start,
                    end_time=remaining[-1].end,
                    text=current_text,
                    tokens=remaining,
                    alignment_fallback=chunk.fallback,
                )
            )
            break

        prefix = _tokens_to_text(remaining)
        cut_chars = _punctuation_cut(prefix, max_chars)
        if cut_chars is None:
            llm_cut = _llm_cut_index(remaining, prefix, max_chars, split_callback)
            if llm_cut is not None:
                cut_index = llm_cut
            else:
                cut_chars = _best_cut(prefix, max_chars)
        if cut_chars is not None:
            count = 0
            cut_index = 0
            for i, token in enumerate(remaining):
                count += len(_normalized(token.text))
                if count >= cut_chars:
                    cut_index = i + 1
                    break
        cut_index = max(1, min(cut_index, len(remaining) - 1))
        selected = remaining[:cut_index]
        parts.append(
            Subtitle(
                start_time=selected[0].start,
                end_time=selected[-1].end,
                text=_tokens_to_text(selected),
                tokens=selected,
                alignment_fallback=chunk.fallback,
            )
        )
        remaining = remaining[cut_index:]
    return parts


def build_subtitles(
    chunks: list[AlignedChunk],
    max_chars: int,
    min_duration: float,
    max_duration: float,
    gap_threshold: float,
    split_callback: Callable[[str, int], tuple[str, str] | None] | None = None,
) -> list[Subtitle]:
    subtitles: list[Subtitle] = []
    for chunk in chunks:
        subtitles.extend(split_aligned_chunk(chunk, max_chars, split_callback))
    subtitles = [s for s in subtitles if s.text.strip()]
    subtitles.sort(key=lambda s: (s.start_time, s.end_time))

    for sub in subtitles:
        if sub.end_time <= sub.start_time:
            sub.end_time = sub.start_time + min_duration
        if sub.end_time - sub.start_time < min_duration:
            sub.end_time = sub.start_time + min_duration
        if sub.end_time - sub.start_time > max_duration:
            sub.end_time = sub.start_time + max_duration

    for i in range(len(subtitles) - 1):
        current = subtitles[i]
        nxt = subtitles[i + 1]
        if current.end_time > nxt.start_time:
            current.end_time = max(current.start_time + min_duration, nxt.start_time)
        elif nxt.start_time - current.end_time <= gap_threshold:
            current.end_time = nxt.start_time
    return subtitles
