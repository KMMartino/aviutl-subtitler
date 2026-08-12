"""Subtitle shaping and splitting."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .models import AlignedChunk, AlignedToken, SplitPlanResult, Subtitle

SENTENCE_BREAK_CHARS = set("。！？!?")
SENTENCE_TERMINAL_SOURCE = "sentence_terminal"
PHRASE_BREAK_CHARS = set("、,;:")
TITLE_JOIN_CHARS = set("・-/&＆＋+")
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
JAPANESE_CLAUSE_ENDINGS = (
    "ということで",
    "と思います",
    "んですけども",
    "ですけども",
    "けども",
    "でした",
    "ました",
    "でしょう",
    "ですね",
    "ですよ",
    "です",
    "ます",
)
JAPANESE_SEMANTIC_BOUNDARY_TAILS = (
    "ということで",
    "けれども",
    "けれど",
    "けども",
    "けど",
    "ならば",
    "なら",
    "れば",
    "たら",
    "ので",
    "のに",
    "から",
    "まで",
    "より",
    "って",
    "では",
    "には",
    "とは",
    "ても",
    "でも",
    "が",
    "を",
    "に",
    "で",
    "は",
    "も",
    "と",
    "へ",
)
JAPANESE_STRONG_CLAUSE_BOUNDARY_TAILS = (
    "ということで",
    "については",
    "けれども",
    "けれど",
    "けども",
    "けど",
    "であれば",
    "ならば",
    "なら",
    "れば",
    "たら",
    "ので",
    "のに",
    "ですが",
    "ますが",
)
JAPANESE_PROTECTED_GRAMMATICAL_PHRASES = (
    "であれば",
    "である",
    "に合わせて",
)
JAPANESE_NUMERAL_CHARS = "〇零一二三四五六七八九十百千万億兆"
_NUMERIC_CORE = rf"(?:[0-9]+(?:[.,][0-9]+)*|[{JAPANESE_NUMERAL_CHARS}]+)"
_NUMERIC_UNIT = (
    r"(?:年|月|日|時|分|秒|人|名|個|本|枚|台|回|話|巻|章|歳|才|円|ドル|"
    r"万|億|兆|%|％|GB|TB|MB|KB|fps|FPS|Hz|kHz|MHz|GHz|cm|mm|km|kg|g)"
)
_NUMERIC_COMPONENT = rf"(?:(?:午前|午後)?{_NUMERIC_CORE}(?:{_NUMERIC_UNIT})?)"
_WEEKDAY = r"(?:[月火水木金土日]曜日)"
_NUMERIC_EXPRESSION_RE = re.compile(
    rf"(?:第|約)?{_NUMERIC_COMPONENT}"
    rf"(?:(?:{_WEEKDAY})?{_NUMERIC_COMPONENT}|(?:[〜～~\-–—/:：]|から|、){_NUMERIC_COMPONENT})*"
    rf"(?:{_WEEKDAY})?",
    re.IGNORECASE,
)


@dataclass
class TokenSegment:
    tokens: list[AlignedToken]
    source: str


@dataclass(frozen=True)
class BoundaryCandidate:
    index: int
    kind: str
    priority: int
    distance: float


@dataclass(frozen=True)
class BoundaryOption:
    id: str
    zone: int
    index: int


@dataclass(frozen=True, order=True)
class BoundaryPathScore:
    semantic_penalty: int
    balance_penalty: float
    negative_pause: float
    stable_order: tuple[int, ...]


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _is_katakana_char(char: str) -> bool:
    return bool(char) and ("\u30a0" <= char <= "\u30ff" or "\uff66" <= char <= "\uff9f")


def _is_hiragana_char(char: str) -> bool:
    return bool(char) and "\u3040" <= char <= "\u309f"


def _is_kanji_char(char: str) -> bool:
    return bool(char) and (
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        or char in "々〆ヶ"
    )


def _inside_protected_numeric_expression(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return False
    return any(match.start() < index < match.end() for match in _NUMERIC_EXPRESSION_RE.finditer(text))


def _inside_known_grammatical_phrase(text: str, index: int) -> bool:
    for term in JAPANESE_TRAILING_CONNECTIVE_TERMS + JAPANESE_PROTECTED_GRAMMATICAL_PHRASES:
        if len(term) < 2:
            continue
        start = text.find(term)
        while start >= 0:
            if start < index < start + len(term):
                return True
            start = text.find(term, start + 1)
    return False


def _normalized_boundary_text(tokens: list[AlignedToken], index: int) -> tuple[str, int]:
    text = _normalized(_tokens_to_text(tokens))
    prefix_chars = len(_normalized(_tokens_to_text(tokens[:index])))
    return text, prefix_chars


def _is_title_char(char: str) -> bool:
    return bool(char) and (char.isascii() and char.isalnum() or char.isdigit() or _is_katakana_char(char) or char in TITLE_JOIN_CHARS)


def _looks_like_title_run(text: str) -> bool:
    if len(text) < 4:
        return False
    has_join = any(char in TITLE_JOIN_CHARS for char in text)
    ascii_count = sum(1 for char in text if char.isascii() and char.isalnum())
    katakana_count = sum(1 for char in text if _is_katakana_char(char))
    digit_count = sum(1 for char in text if char.isdigit())
    return has_join or ascii_count >= 2 or katakana_count >= 4 or (digit_count > 0 and (ascii_count + katakana_count) > 0)


def _inside_fragile_title_run_text(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return False
    if not (_is_title_char(text[index - 1]) and _is_title_char(text[index])):
        return False
    start = index - 1
    while start > 0 and _is_title_char(text[start - 1]):
        start -= 1
    end = index
    while end < len(text) and _is_title_char(text[end]):
        end += 1
    return _looks_like_title_run(text[start:end])


def _split_inside_fragile_title_run(tokens: list[AlignedToken], index: int) -> bool:
    if index <= 0 or index >= len(tokens):
        return False
    text = _tokens_to_text(tokens)
    prefix = _normalized(_tokens_to_text(tokens[:index]))
    return _inside_fragile_title_run_text(_normalized(text), len(prefix))


def _normalized_len(tokens: list[AlignedToken]) -> int:
    return len(_normalized(_tokens_to_text(tokens)))


def _token_duration(tokens: list[AlignedToken]) -> float:
    if not tokens:
        return 0.0
    return max(0.0, tokens[-1].end - tokens[0].start)


def _candidate_distance(
    tokens: list[AlignedToken],
    index: int,
    target_chars: int,
    max_duration: float,
) -> float:
    distance = float(abs(_normalized_len(tokens[:index]) - target_chars))
    if max_duration > 0:
        duration = _token_duration(tokens[:index])
        distance += max(0.0, duration - max_duration) * max(1.0, target_chars)
    return distance


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


def _ends_with_sentence_break(tokens: list[AlignedToken]) -> bool:
    if not tokens:
        return False
    text = _tokens_to_text(tokens).rstrip()
    return bool(text) and text[-1] in SENTENCE_BREAK_CHARS


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
    left_text = _normalized(tokens[index - 1].text)
    right_text = _normalized(tokens[index].text)
    if not left_text or not right_text:
        return False
    left = left_text[-1]
    right = right_text[0]
    if left == "." and right.isdigit():
        return False
    if left.isdigit() and right == ".":
        return False
    if left.isascii() and right.isascii() and left.isalnum() and right.isalnum():
        return False
    if _is_katakana_char(left) and _is_katakana_char(right):
        return False
    if _is_kanji_char(left) and _is_kanji_char(right):
        return False
    text, char_index = _normalized_boundary_text(tokens, index)
    if _inside_protected_numeric_expression(text, char_index):
        return False
    if _inside_known_grammatical_phrase(text, char_index):
        return False
    if _split_inside_fragile_title_run(tokens, index):
        return False
    return True


def _is_legal_boundary(tokens: list[AlignedToken], index: int) -> bool:
    if not _is_safe_boundary(tokens, index):
        return False
    return _trailing_connective_phrase_end(tokens, index) is None


def _semantic_boundary_penalty(tokens: list[AlignedToken], index: int) -> int:
    """Rank legal Japanese boundaries without pretending to be a tokenizer."""
    if index <= 0 or index >= len(tokens):
        return 99
    left_text = _normalized(_tokens_to_text(tokens[:index]))
    right_text = _normalized(_tokens_to_text(tokens[index:]))
    if not left_text or not right_text:
        return 99
    left = left_text[-1]
    right = right_text[0]
    if left in SENTENCE_BREAK_CHARS or left in PHRASE_BREAK_CHARS:
        return 0
    if any(left_text.endswith(tail) for tail in JAPANESE_STRONG_CLAUSE_BOUNDARY_TAILS):
        return 0
    if any(left_text.endswith(tail) for tail in JAPANESE_SEMANTIC_BOUNDARY_TAILS) and (
        _is_kanji_char(right)
        or _is_katakana_char(right)
        or right.isdigit()
        or (right.isascii() and right.isalnum())
    ):
        return 0
    if _is_hiragana_char(left) and (
        _is_kanji_char(right)
        or _is_katakana_char(right)
        or right.isdigit()
        or (right.isascii() and right.isalnum())
    ):
        return 1
    if _is_hiragana_char(left) and _is_hiragana_char(right):
        return 4
    if (_is_kanji_char(left) or _is_katakana_char(left)) and _is_hiragana_char(right):
        return 3
    return 2


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
    left_text = _normalized(_tokens_to_text(tokens[:index]))
    if any(left_text.endswith(ending) for ending in JAPANESE_CLAUSE_ENDINGS):
        return "structural_clause", 1
    if _ends_with_trailing_connective_phrase(tokens, index):
        return "structural_connective", 2
    if previous_text and previous_text[-1] in PHRASE_BREAK_CHARS:
        return "structural_phrase", 2
    return None


def _boundary_candidates(
    tokens: list[AlignedToken],
    max_chars: int,
    target_chars: int,
    max_duration: float = 0.0,
) -> list[BoundaryCandidate]:
    candidates: dict[tuple[int, str], BoundaryCandidate] = {}
    for raw_index in range(1, len(tokens)):
        index = _normalized_boundary(tokens, raw_index, max_chars)
        if index is None:
            continue
        kind_priority = _classify_boundary(tokens, index)
        distance = _candidate_distance(tokens, index, target_chars, max_duration)
        if kind_priority is not None:
            kind, priority = kind_priority
            candidates[(index, kind)] = BoundaryCandidate(index, kind, priority, distance)
        gap = max(0.0, tokens[index].start - tokens[index - 1].end)
        if gap >= 0.08:
            candidates[(index, "acoustic_pause")] = BoundaryCandidate(
                index,
                "acoustic_pause",
                3,
                distance - min(gap, 1.0) * max_chars,
            )

    if max_duration > 0 and _token_duration(tokens) > max_duration:
        duration_indexes = [
            index
            for index in range(1, len(tokens))
            if _is_legal_boundary(tokens, index)
            and _token_duration(tokens[:index]) <= max_duration
        ]
        if duration_indexes:
            index = max(duration_indexes, key=lambda item: _token_duration(tokens[:item]))
            candidates[(index, "duration_boundary")] = BoundaryCandidate(
                index,
                "duration_boundary",
                5,
                _candidate_distance(tokens, index, target_chars, max_duration),
            )

    max_index = _max_char_boundary(tokens, max_chars)
    if max_index is not None:
        candidates[(max_index, "max_chars_boundary")] = BoundaryCandidate(
            max_index,
            "max_chars_boundary",
            5,
            _candidate_distance(tokens, max_index, target_chars, max_duration),
        )
    return sorted(candidates.values(), key=lambda item: (item.priority, item.distance, item.index))


def _best_boundary_candidate(
    tokens: list[AlignedToken],
    max_chars: int,
    max_duration: float = 0.0,
) -> BoundaryCandidate | None:
    if len(tokens) <= 1:
        return None
    target_chars = max(1, min(max_chars, _normalized_len(tokens) // 2))
    candidates = _boundary_candidates(tokens, max_chars, target_chars, max_duration)
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


def _span_duration(tokens: list[AlignedToken], start: int, end: int) -> float:
    if start >= end:
        return 0.0
    return max(0.0, tokens[end - 1].end - tokens[start].start)


def _span_within_limits(
    tokens: list[AlignedToken],
    start: int,
    end: int,
    max_chars: int,
    max_duration: float,
) -> bool:
    return (
        _normalized_len(tokens[start:end]) <= max_chars
        and (max_duration <= 0 or _span_duration(tokens, start, end) <= max_duration)
    )


def _span_utilization(
    tokens: list[AlignedToken],
    start: int,
    end: int,
    max_chars: int,
    max_duration: float,
) -> float:
    char_ratio = _normalized_len(tokens[start:end]) / max(1, max_chars)
    duration_ratio = _span_duration(tokens, start, end) / max_duration if max_duration > 0 else 0.0
    return max(char_ratio, duration_ratio)


def _boundary_options(
    tokens: list[AlignedToken],
    max_chars: int,
    max_duration: float,
    *,
    multiple: bool,
) -> list[BoundaryOption]:
    """Build a small legal lattice near each upcoming hard frontier.

    Deterministic sentence, clause, pause, and phrase boundaries have already
    been exhausted before this is called. Each zone therefore offers the model
    only a handful of otherwise ambiguous positions spanning roughly 60–100%
    of a local budget or 75–100% of each compatible hosted frontier band.
    """
    options: list[BoundaryOption] = []
    cursor = 0
    zone = 1
    while cursor < len(tokens) - 1:
        remainder = TokenSegment(tokens[cursor:], "candidate_lattice")
        if not _over_limit(remainder, max_chars, max_duration):
            break
        if cursor:
            deterministic = _best_boundary_candidate(remainder.tokens, max_chars, max_duration)
            if deterministic is not None and deterministic.priority < 4:
                break
        legal = [
            index
            for index in range(cursor + 1, len(tokens))
            if _is_legal_boundary(tokens, index)
            and _span_within_limits(tokens, cursor, index, max_chars, max_duration)
        ]
        if not legal:
            break
        needs_multiple_zones = (
            _normalized_len(remainder.tokens) > max_chars * 2
            or (max_duration > 0 and _token_duration(remainder.tokens) > max_duration * 2)
        )
        compatible_multi_zone = multiple and needs_multiple_zones
        targets = (
            (0.75, 0.80, 0.85, 0.90, 0.98)
            if compatible_multi_zone
            else (0.60, 0.70, 0.80, 0.90, 0.98)
        )
        preferred = [
            index
            for index in legal
            if _span_utilization(tokens, cursor, index, max_chars, max_duration)
            >= (0.70 if compatible_multi_zone else 0.55)
        ] or legal
        minimum_tail_chars = max(6, max_chars // 3)
        balanced = [
            index
            for index in preferred
            if _over_limit(TokenSegment(tokens[index:], "candidate_tail"), max_chars, max_duration)
            or _normalized_len(tokens[index:]) >= minimum_tail_chars
            or (max_duration > 0 and _token_duration(tokens[index:]) >= max_duration * 0.4)
        ]
        if balanced:
            preferred = balanced
        best_semantic_penalty = min(_semantic_boundary_penalty(tokens, index) for index in preferred)
        semantic_candidates = [
            index
            for index in preferred
            if _semantic_boundary_penalty(tokens, index) == best_semantic_penalty
        ]
        if semantic_candidates:
            preferred = semantic_candidates
        selected_indexes: list[int] = []
        for target in targets:
            index = min(
                preferred,
                key=lambda item: (
                    abs(_span_utilization(tokens, cursor, item, max_chars, max_duration) - target),
                    -item,
                ),
            )
            if index not in selected_indexes:
                selected_indexes.append(index)
        selected_indexes.sort()
        for letter_index, index in enumerate(selected_indexes):
            options.append(BoundaryOption(f"Z{zone}{chr(ord('A') + letter_index)}", zone, index))
        # Multi-zone bands advance from the earliest offered position. With a
        # 75–100% band, every choice in the next zone remains within one full
        # budget of every choice in the current zone. This avoids presenting
        # hosted models with combinations that cannot pass span validation.
        cursor = min(selected_indexes) if compatible_multi_zone else max(legal)
        zone += 1
        if not multiple:
            break
    return options


def _annotate_boundary_options(tokens: list[AlignedToken], options: list[BoundaryOption]) -> str:
    by_index: dict[int, list[str]] = {}
    for option in options:
        by_index.setdefault(option.index, []).append(option.id)
    pieces: list[str] = []
    for index, token in enumerate(tokens):
        if index in by_index:
            pieces.append("".join(f"⟦{candidate_id}⟧" for candidate_id in by_index[index]))
        pieces.append(token.text)
    return "".join(pieces)


def _choose_returned_boundary_options(
    tokens: list[AlignedToken],
    options: list[BoundaryOption],
    returned_ids: list[str],
    max_chars: int,
    max_duration: float,
    *,
    multiple: bool,
) -> list[BoundaryOption]:
    """Resolve duplicate zone answers using the complete timed boundary path."""
    option_by_id = {option.id: option for option in options}
    if not multiple:
        for candidate_id in returned_ids:
            option = option_by_id.get(candidate_id)
            if option is not None:
                return [option]
        return []

    choices_by_zone: dict[int, list[BoundaryOption]] = {}
    for candidate_id in returned_ids:
        option = option_by_id.get(candidate_id)
        if option is None:
            continue
        choices = choices_by_zone.setdefault(option.zone, [])
        if option not in choices:
            choices.append(option)
    if not choices_by_zone:
        return []

    stable_order = {option.id: index for index, option in enumerate(options)}
    states: dict[int, tuple[BoundaryPathScore, list[BoundaryOption]]] = {
        0: (BoundaryPathScore(0, 0.0, 0.0, ()), [])
    }
    completed_zones = 0
    for zone in sorted(choices_by_zone):
        next_states: dict[int, tuple[BoundaryPathScore, list[BoundaryOption]]] = {}
        for option in choices_by_zone[zone]:
            for cursor, (score, path) in states.items():
                if option.index <= cursor:
                    continue
                if not _span_within_limits(tokens, cursor, option.index, max_chars, max_duration):
                    continue
                utilization = _span_utilization(tokens, cursor, option.index, max_chars, max_duration)
                pause = max(0.0, tokens[option.index].start - tokens[option.index - 1].end)
                candidate_score = BoundaryPathScore(
                    semantic_penalty=(
                        score.semantic_penalty
                        + _semantic_boundary_penalty(tokens, option.index)
                    ),
                    balance_penalty=score.balance_penalty + abs(utilization - 0.8),
                    negative_pause=score.negative_pause - min(pause, 1.0),
                    stable_order=score.stable_order + (stable_order[option.id],),
                )
                previous = next_states.get(option.index)
                if previous is None or candidate_score < previous[0]:
                    next_states[option.index] = (candidate_score, path + [option])
        if not next_states:
            break
        states = next_states
        completed_zones += 1
    if not completed_zones:
        return []
    return min(states.values(), key=lambda item: item[0])[1]


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


def _over_limit(segment: TokenSegment, max_chars: int, max_duration: float = 0.0) -> bool:
    return (
        len(_normalized(_tokens_to_text(segment.tokens))) > max_chars
        or (max_duration > 0 and _token_duration(segment.tokens) > max_duration)
    )


def _llm_boundary_candidates(
    segment: TokenSegment,
    max_chars: int,
    max_duration: float,
    llm_splitter,
    llm_split_callback: Callable[[SplitPlanResult, int, int, int, str], None] | None,
    attempt_index: int,
    pass_name: str,
) -> list[BoundaryCandidate]:
    text = _tokens_to_text(segment.tokens)
    sentence_break_count = sum(1 for char in text if char in SENTENCE_BREAK_CHARS)
    connective_break_count = sum(text.count(term) for term in JAPANESE_TRAILING_CONNECTIVE_TERMS)
    input_chars = len(_normalized(text))
    multiple = bool(getattr(llm_splitter, "supports_multi_split", lambda: False)())
    options = _boundary_options(
        segment.tokens,
        max_chars,
        max_duration,
        multiple=multiple,
    )
    if not options:
        return []
    candidate_ids = [option.id for option in options]
    result = llm_splitter.select_split_boundaries(
        text,
        _annotate_boundary_options(segment.tokens, options),
        candidate_ids,
        max_chars,
        multiple=multiple,
    )
    result.sentence_break_count = sentence_break_count
    result.connective_break_count = connective_break_count
    returned_ids = (
        result.parsed_ids
        if multiple and result.parsed_ids
        else result.selected_ids or []
    )
    selected_options = _choose_returned_boundary_options(
        segment.tokens,
        options,
        returned_ids,
        max_chars,
        max_duration,
        multiple=multiple,
    )
    accepted: list[BoundaryCandidate] = []
    cursor = 0
    for option in selected_options:
        if not _span_within_limits(segment.tokens, cursor, option.index, max_chars, max_duration):
            break
        accepted.append(
            BoundaryCandidate(
                index=option.index,
                kind="llm_boundary",
                priority=4,
                distance=abs(_normalized_len(segment.tokens[cursor : option.index]) - max_chars),
            )
        )
        cursor = option.index
    if not accepted:
        result.accepted = False
        if returned_ids:
            result.reject_reason = "selected_boundary_span_invalid"
    else:
        result.selected_ids = [
            option.id
            for candidate in accepted
            for option in options
            if option.index == candidate.index
        ]
        result.valid_id_count = len(result.selected_ids)
        result.accepted = True
        result.reject_reason = "none"
    if llm_split_callback is not None:
        llm_split_callback(result, attempt_index, input_chars, len(segment.tokens), pass_name)
    return accepted


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
    max_duration: float = 0.0,
    deterministic_candidate: BoundaryCandidate | None = None,
) -> list[TokenSegment]:
    if not _over_limit(segment, max_chars, max_duration):
        return [segment]
    candidates = []
    if deterministic_candidate is None:
        deterministic_candidate = _best_boundary_candidate(segment.tokens, max_chars, max_duration)
    if deterministic_candidate is not None:
        candidates.append(deterministic_candidate)
    candidate = candidates[0] if candidates else None
    if candidate is None:
        hard = _hard_boundary(segment.tokens, max_chars)
        if hard is None:
            return [segment]
        candidate = BoundaryCandidate(
            hard,
            "max_chars_boundary",
            5,
            _candidate_distance(segment.tokens, hard, max_chars, max_duration),
        )
    if candidate.index <= 0 or candidate.index >= len(segment.tokens):
        return [segment]
    left_tokens = segment.tokens[: candidate.index]
    right_tokens = segment.tokens[candidate.index :]
    left_source = _source_with(segment.source, candidate.kind)
    if candidate.kind == "structural_sentence" or _ends_with_sentence_break(left_tokens):
        left_source = _source_with(left_source, SENTENCE_TERMINAL_SOURCE)
    left = TokenSegment(left_tokens, left_source)
    right = TokenSegment(right_tokens, _source_with(segment.source, candidate.kind))
    if len(left.tokens) == len(segment.tokens) or len(right.tokens) == len(segment.tokens):
        return [segment]
    return [left, right]


def _split_segment_at_candidates(
    segment: TokenSegment,
    candidates: list[BoundaryCandidate],
) -> list[TokenSegment]:
    indexes = sorted({candidate.index for candidate in candidates if 0 < candidate.index < len(segment.tokens)})
    if not indexes:
        return [segment]
    parts: list[TokenSegment] = []
    cursor = 0
    for index in indexes + [len(segment.tokens)]:
        tokens = segment.tokens[cursor:index]
        if tokens:
            source = _source_with(segment.source, "llm_boundary")
            parts.append(TokenSegment(tokens, source))
        cursor = index
    return parts if len(parts) > 1 else [segment]


def _assert_or_repair_connective_heads(
    segments: list[TokenSegment],
    max_chars: int,
    max_duration: float = 0.0,
) -> list[TokenSegment]:
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
        if (
            _normalized_len(previous.tokens + moved) <= max_chars
            and (max_duration <= 0 or _token_duration(previous.tokens + moved) <= max_duration)
        ):
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
) -> list[Subtitle]:
    attempt_index = 0
    segments = [TokenSegment(tokens[:], "initial")]

    def maybe_llm_candidates(segment: TokenSegment, pass_name: str) -> list[BoundaryCandidate]:
        nonlocal attempt_index
        if llm_splitter is None:
            return []
        capacity = getattr(
            llm_splitter,
            "split_input_capacity",
            lambda chars: max(96, chars * 4),
        )(max_chars)
        planning_tokens: list[AlignedToken] = []
        chars = 0
        for token in segment.tokens:
            token_chars = len(_normalized(token.text))
            if planning_tokens and chars + token_chars > capacity:
                break
            planning_tokens.append(token)
            chars += token_chars
        if len(planning_tokens) < 2:
            return []
        planning_segment = TokenSegment(planning_tokens, segment.source)
        attempt_index += 1
        return _llm_boundary_candidates(
            planning_segment,
            max_chars,
            max_duration,
            llm_splitter,
            llm_split_callback,
            attempt_index,
            pass_name,
        )

    pending = segments
    completed: list[TokenSegment] = []
    while pending:
        segment = pending.pop(0)
        if not _over_limit(segment, max_chars, max_duration):
            completed.append(segment)
            continue
        pass_name = "llm_boundary" if not completed else "llm_boundary_retry"
        deterministic_candidate = _best_boundary_candidate(
            segment.tokens,
            max_chars,
            max_duration,
        )
        llm_candidates: list[BoundaryCandidate] = []
        if deterministic_candidate is None or deterministic_candidate.priority >= 4:
            llm_candidates = maybe_llm_candidates(segment, pass_name)
        if llm_candidates:
            split_parts = _split_segment_at_candidates(segment, llm_candidates)
        else:
            split_parts = _split_segment(
                segment,
                max_chars,
                max_duration,
                deterministic_candidate=deterministic_candidate,
            )
        if len(split_parts) == 1 and split_parts[0] is segment:
            completed.append(segment)
            continue
        pending = split_parts + pending
    segments = _assert_or_repair_connective_heads(completed, max_chars, max_duration)
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
