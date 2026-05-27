"""Subtitle shaping and splitting."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .models import AlignedChunk, AlignedToken, SplitPlanResult, Subtitle

SENTENCE_BREAK_CHARS = set("。！？!?")
PHRASE_BREAK_CHARS = set("、,;:")
JAPANESE_TRAILING_CONNECTIVE_TERMS = (
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
JAPANESE_CONNECTIVES = JAPANESE_TRAILING_CONNECTIVE_TERMS


@dataclass
class TokenSegment:
    tokens: list[AlignedToken]
    source: str


@dataclass(frozen=True)
class BoundaryCandidate:
    index: int
    kind: str
    priority: int
    distance: int


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


def _source_with(source: str, label: str) -> str:
    if not source or source == "initial":
        return label
    labels = source.split("+")
    if label not in labels:
        labels.append(label)
    return "+".join(labels)


def _token_count_for_normalized_prefix(tokens: list[AlignedToken], prefix: str) -> int | None:
    seen = ""
    for index, token in enumerate(tokens, start=1):
        seen += _normalized(token.text)
        if len(seen) >= len(prefix):
            return index if seen == prefix else None
    return None


def _trailing_connective_phrase_end(tokens: list[AlignedToken], index: int) -> int | None:
    if index < 0 or index >= len(tokens):
        return None
    right_text = _normalized(_tokens_to_text(tokens[index:]))
    if not right_text:
        return None
    for term in sorted(JAPANESE_TRAILING_CONNECTIVE_TERMS, key=len, reverse=True):
        for punctuation in PHRASE_BREAK_CHARS:
            phrase = f"{term}{punctuation}"
            if right_text.startswith(phrase):
                count = _token_count_for_normalized_prefix(tokens[index:], phrase)
                if count is None:
                    return None
                phrase_end = index + count
                return phrase_end if phrase_end <= len(tokens) else None
    return None


def _ends_with_trailing_connective_phrase(tokens: list[AlignedToken], index: int) -> bool:
    left_text = _normalized(_tokens_to_text(tokens[:index]))
    return any(
        left_text.endswith(f"{term}{punctuation}")
        for term in JAPANESE_TRAILING_CONNECTIVE_TERMS
        for punctuation in PHRASE_BREAK_CHARS
    )


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


def _is_legal_boundary(tokens: list[AlignedToken], index: int) -> bool:
    if not _is_safe_boundary(tokens, index):
        return False
    return _trailing_connective_phrase_end(tokens, index) is None


def _normalized_boundary(tokens: list[AlignedToken], index: int, max_chars: int) -> int | None:
    if _is_legal_boundary(tokens, index):
        return index
    phrase_end = _trailing_connective_phrase_end(tokens, index)
    if phrase_end is None or phrase_end >= len(tokens):
        return None
    if _normalized_len(tokens[:phrase_end]) > max_chars:
        return None
    return phrase_end if _is_legal_boundary(tokens, phrase_end) else None


def _classify_boundary(tokens: list[AlignedToken], index: int) -> tuple[str, int] | None:
    previous_text = tokens[index - 1].text
    if previous_text and previous_text[-1] in SENTENCE_BREAK_CHARS:
        return "structural_sentence", 0
    if _ends_with_trailing_connective_phrase(tokens, index):
        return "structural_connective", 1
    if previous_text and previous_text[-1] in PHRASE_BREAK_CHARS:
        return "structural_phrase", 2
    return None


def _boundary_candidates(tokens: list[AlignedToken], max_chars: int, target_chars: int) -> list[BoundaryCandidate]:
    candidates: dict[tuple[int, str], BoundaryCandidate] = {}
    for raw_index in range(1, len(tokens)):
        index = _normalized_boundary(tokens, raw_index, max_chars)
        if index is None:
            continue
        kind_priority = _classify_boundary(tokens, index)
        if kind_priority is None:
            continue
        kind, priority = kind_priority
        distance = abs(_normalized_len(tokens[:index]) - target_chars)
        candidates[(index, kind)] = BoundaryCandidate(index, kind, priority, distance)

    max_index = _max_char_boundary(tokens, max_chars)
    if max_index is not None:
        candidates[(max_index, "max_chars_boundary")] = BoundaryCandidate(
            max_index,
            "max_chars_boundary",
            4,
            abs(_normalized_len(tokens[:max_index]) - target_chars),
        )
    return sorted(candidates.values(), key=lambda item: (item.priority, item.distance, item.index))


def _best_boundary_candidate(tokens: list[AlignedToken], max_chars: int) -> BoundaryCandidate | None:
    if len(tokens) <= 1:
        return None
    target_chars = max(1, min(max_chars, _normalized_len(tokens) // 2))
    candidates = _boundary_candidates(tokens, max_chars, target_chars)
    return candidates[0] if candidates else None


def _max_char_boundary(tokens: list[AlignedToken], max_chars: int) -> int | None:
    count = 0
    raw_index = None
    for index, token in enumerate(tokens, start=1):
        count += len(_normalized(token.text))
        if count >= max_chars:
            raw_index = min(index, len(tokens) - 1)
            break
    if raw_index is None:
        return None
    for candidate in (raw_index, raw_index - 1, raw_index + 1):
        index = _normalized_boundary(tokens, candidate, max_chars)
        if index is not None:
            return index
    legal = [
        index
        for index in range(1, len(tokens))
        if _normalized_len(tokens[:index]) <= max_chars and _is_legal_boundary(tokens, index)
    ]
    if not legal:
        return None
    return min(legal, key=lambda item: abs(_normalized_len(tokens[:item]) - max_chars))


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


def _llm_boundary_candidate(
    segment: TokenSegment,
    max_chars: int,
    llm_splitter,
    llm_split_callback: Callable[[SplitPlanResult, int, int, int, str], None] | None,
    attempt_index: int,
    pass_name: str,
) -> BoundaryCandidate | None:
    text = _tokens_to_text(segment.tokens)
    sentence_break_count = sum(1 for char in text if char in SENTENCE_BREAK_CHARS)
    connective_break_count = sum(text.count(term) for term in JAPANESE_TRAILING_CONNECTIVE_TERMS)
    input_chars = len(_normalized(text))
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
    candidate: BoundaryCandidate | None = None
    if not lines or len(lines) != 2:
        result.accepted = False
        result.reject_reason = "wrong_line_count" if lines else result.reject_reason
    else:
        counts = _map_lines_to_token_counts(segment.tokens, lines)
        if counts is None:
            result.accepted = False
            result.reject_reason = "line_not_substring"
        else:
            raw_index = counts[0]
            index = _normalized_boundary(segment.tokens, raw_index, max_chars)
            if index is None:
                result.accepted = False
                result.reject_reason = "llm_boundary_rejected_illegal_head"
            else:
                result.lines = lines
                result.accepted = True
                result.reject_reason = "llm_boundary_repaired" if index != raw_index else "none"
                kind = "llm_boundary_repaired" if index != raw_index else "llm_boundary"
                candidate = BoundaryCandidate(
                    index=index,
                    kind=kind,
                    priority=3,
                    distance=abs(_normalized_len(segment.tokens[:index]) - max_chars),
                )
    if llm_split_callback is not None:
        llm_split_callback(result, attempt_index, input_chars, len(segment.tokens), pass_name)
    return candidate


def _hard_boundary(tokens: list[AlignedToken], max_chars: int) -> int | None:
    boundary = _max_char_boundary(tokens, max_chars)
    if boundary is not None:
        return boundary
    count = 0
    for index, token in enumerate(tokens, start=1):
        count += len(_normalized(token.text))
        if count >= max_chars:
            return max(1, min(index, len(tokens) - 1))
    return len(tokens) - 1 if len(tokens) > 1 else None


def _split_segment(
    segment: TokenSegment,
    max_chars: int,
    llm_candidate: BoundaryCandidate | None = None,
    deterministic_candidate: BoundaryCandidate | None = None,
) -> list[TokenSegment]:
    if not _over_limit(segment, max_chars):
        return [segment]
    candidates = []
    if deterministic_candidate is None:
        deterministic_candidate = _best_boundary_candidate(segment.tokens, max_chars)
    if deterministic_candidate is not None:
        candidates.append(deterministic_candidate)
    if llm_candidate is not None:
        candidates.append(llm_candidate)
        candidates.sort(key=lambda item: (item.priority, item.distance, item.index))
    candidate = candidates[0] if candidates else None
    if candidate is None:
        hard = _hard_boundary(segment.tokens, max_chars)
        if hard is None:
            return [segment]
        candidate = BoundaryCandidate(
            hard,
            "max_chars_boundary",
            4,
            abs(_normalized_len(segment.tokens[:hard]) - max_chars),
        )
    if candidate.index <= 0 or candidate.index >= len(segment.tokens):
        return [segment]
    left = TokenSegment(segment.tokens[: candidate.index], _source_with(segment.source, candidate.kind))
    right = TokenSegment(segment.tokens[candidate.index :], _source_with(segment.source, candidate.kind))
    if len(left.tokens) == len(segment.tokens) or len(right.tokens) == len(segment.tokens):
        return [segment]
    return [left, right]


def _assert_or_repair_connective_heads(segments: list[TokenSegment], max_chars: int) -> list[TokenSegment]:
    if len(segments) <= 1:
        return segments
    repaired: list[TokenSegment] = [segments[0]]
    for segment in segments[1:]:
        previous = repaired[-1]
        phrase_end = _trailing_connective_phrase_end(segment.tokens, 0)
        if phrase_end is None:
            repaired.append(segment)
            continue
        moved = segment.tokens[:phrase_end]
        remaining = segment.tokens[phrase_end:]
        if _normalized_len(previous.tokens + moved) <= max_chars:
            previous.tokens.extend(moved)
            previous.source = _source_with(previous.source, "boundary_repaired")
            if remaining:
                repaired.append(TokenSegment(remaining, _source_with(segment.source, "boundary_repaired")))
            continue
        segment.source = _source_with(segment.source, "connective_head_unrepaired")
        repaired.append(segment)
    return repaired


def split_token_chain(
    tokens: list[AlignedToken],
    max_chars: int,
    max_duration: float,
    fallback: bool = False,
    llm_splitter=None,
    llm_split_callback: Callable[[SplitPlanResult, int, int, int, str], None] | None = None,
    llm_max_input_chars: int = 240,
    llm_second_pass_max_input_chars: int = 240,
) -> list[Subtitle]:
    attempt_index = 0
    segments = [TokenSegment(tokens[:], "initial")]

    def maybe_llm_candidate(segment: TokenSegment, pass_name: str, input_limit: int) -> BoundaryCandidate | None:
        nonlocal attempt_index
        text = _tokens_to_text(segment.tokens)
        sentence_break_count = sum(1 for char in text if char in SENTENCE_BREAK_CHARS)
        connective_break_count = sum(text.count(term) for term in JAPANESE_TRAILING_CONNECTIVE_TERMS)
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
            return None
        return _llm_boundary_candidate(
            segment,
            max_chars,
            llm_splitter,
            llm_split_callback,
            attempt_index,
            pass_name,
        )

    pending = segments
    completed: list[TokenSegment] = []
    while pending:
        segment = pending.pop(0)
        if not _over_limit(segment, max_chars):
            completed.append(segment)
            continue
        input_limit = llm_max_input_chars if not completed else llm_second_pass_max_input_chars
        pass_name = "llm_boundary" if not completed else "llm_boundary_retry"
        deterministic_candidate = _best_boundary_candidate(segment.tokens, max_chars)
        llm_candidate = None
        if deterministic_candidate is None or deterministic_candidate.priority >= 4:
            llm_candidate = maybe_llm_candidate(segment, pass_name, input_limit)
        split_parts = _split_segment(
            segment,
            max_chars,
            llm_candidate,
            deterministic_candidate,
        )
        if len(split_parts) == 1 and split_parts[0] is segment:
            completed.append(segment)
            continue
        pending = split_parts + pending
    segments = _assert_or_repair_connective_heads(completed, max_chars)
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
    if split_callback is None:
        return split_token_chain(
            chunk.tokens,
            max_chars=max_chars,
            max_duration=chunk.chunk.end - chunk.chunk.start,
            fallback=chunk.fallback,
        )

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
