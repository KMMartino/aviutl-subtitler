"""Source-exact communicative units and conservative semantic cut constraints."""
from __future__ import annotations

import copy
import math
import unicodedata
from typing import Any, Callable

from .errors import SubtitlerError
from .operation_store import content_digest
from .transcript_document import TranscriptDocument


def _punctuation(value: str) -> bool:
    return all(char.isspace() or unicodedata.category(char).startswith("P") for char in value)


def _local_uncertainty(pieces: list[dict[str, Any]], prefix: str,
                       segment_start: int, segment_end: int) -> list[dict[str, Any]]:
    """Bind untimed lexical runs to existing anchors without retiming characters."""
    groups: list[tuple[int, int]] = []
    for index, piece in enumerate(pieces):
        if piece["start_ms"] != piece["end_ms"]:
            continue
        left, right = index, index
        while left > 0 and pieces[left]["start_ms"] == pieces[left]["end_ms"]:
            left -= 1
        while right + 1 < len(pieces) and pieces[right]["start_ms"] == pieces[right]["end_ms"]:
            right += 1
        if groups and left <= groups[-1][1]:
            groups[-1] = (groups[-1][0], max(right, groups[-1][1]))
        else:
            groups.append((left, right))
    result, cursor = [], 0
    for left, right in groups:
        result.extend(pieces[cursor:left])
        selected = pieces[left:right + 1]
        start = min(p["start_ms"] for p in selected)
        end = max(p["end_ms"] for p in selected)
        if selected[0]["start_ms"] == selected[0]["end_ms"]:
            start = segment_start
        if selected[-1]["start_ms"] == selected[-1]["end_ms"]:
            end = segment_end
        if not segment_start <= start < end <= segment_end:
            return []  # No trustworthy local envelope: retain the source segment.
        result.append({"token_id": f"{prefix}-opaque-{left}-{right}",
                       "text": "".join(p["text"] for p in selected),
                       "start_ms": start, "end_ms": end, "alignment": "opaque_span",
                       "alignment_limitation": "Zero-duration lexical alignment retained inside original neighboring anchor bounds; internal timing remains unknown.",
                       "original_token_ids": [ref for p in selected for ref in p["original_token_ids"]],
                       "alignment_anchors": [{key: p[key] for key in ("token_id", "start_ms", "end_ms")}
                                             for p in selected if p["start_ms"] < p["end_ms"]],
                       "uncertain_tokens": [{key: p[key] for key in ("token_id", "text", "start_ms", "end_ms")}
                                            for p in selected if p["start_ms"] == p["end_ms"]]})
        cursor = right + 1
    return result + pieces[cursor:]


def _tokens(document: TranscriptDocument, source_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    duration = round(document.duration_sec * 1000)
    for segment in sorted(document.backend.segments, key=lambda s: (s.start, s.index)):
        if not segment.text.strip():
            continue
        prefix = f"{source_id}-segment-{segment.index}"
        original_ids = [f"{prefix}-token-{i}" for i in range(len(segment.tokens))]
        aligned = bool(segment.tokens) and not segment.fallback_timing and segment.timing_kind not in ("none", "segment")
        pieces: list[dict[str, Any]] = []
        cursor = 0
        previous = -1
        previous_raw = -1
        for index, token in enumerate(segment.tokens):
            # CTC punctuation often has a zero-duration alignment. It is text,
            # not an independently spoken atom; retain its ID with a neighbor.
            if _punctuation(token.text):
                continue
            word = token.text.strip()
            position = segment.text.find(word, cursor) if word else -1
            if (position < 0 or not _punctuation(segment.text[cursor:position])
                    or token.start is None or token.end is None
                    or not math.isfinite(token.start) or not math.isfinite(token.end)):
                aligned = False
                break
            start, end = round(token.start * 1000), round(token.end * 1000)
            if not 0 <= start <= end <= duration or token.end < token.start or start < previous:
                aligned = False
                break
            stop = position + len(word)
            pieces.append({"token_id": original_ids[index], "text": segment.text[cursor:stop],
                           "start_ms": start, "end_ms": end, "alignment": "word",
                           "token_kind": token.kind,
                           "original_token_ids": original_ids[previous_raw + 1:index + 1]})
            cursor, previous, previous_raw = stop, start, index
        if not _punctuation(segment.text[cursor:]):
            aligned = False
        if aligned and pieces:
            pieces[-1]["text"] += segment.text[cursor:]
            pieces[-1]["original_token_ids"].extend(original_ids[previous_raw + 1:])
            pieces = _local_uncertainty(pieces, prefix, round(segment.start * 1000), round(segment.end * 1000))
        if not aligned or not pieces:
            start, end = round(segment.start * 1000), round(segment.end * 1000)
            if not 0 <= start < end <= duration:
                raise SubtitlerError("Unaligned speech segment has invalid source bounds")
            pieces = [{"token_id": f"{prefix}-opaque", "text": segment.text,
                       "start_ms": start, "end_ms": end, "alignment": "opaque_segment",
                       "alignment_limitation": "Original segment retained because lexical timing or exact text coverage is incomplete.",
                       "original_token_ids": original_ids}]
        if result:
            pieces[0]["text"] = "\n" + pieces[0]["text"]
        result.extend(pieces)
    return [{**token, "index": index} for index, token in enumerate(result)]


def _schema(start: int, end: int) -> dict[str, Any]:
    properties = {"start_index": {"type": "integer", "minimum": start, "maximum": end - 1},
                  "end_index": {"type": "integer", "minimum": start + 1, "maximum": end},
                  "meaning": {"type": "string"},
                  "dependency_starts": {"type": "array", "items": {"type": "integer", "minimum": 0,
                                                                         "maximum": end - 1}}}
    return {"type": "object", "additionalProperties": False, "required": ["units"], "properties": {
        "units": {"type": "array", "minItems": 1, "maxItems": min(200, end - start),
                  "items": {"type": "object", "additionalProperties": False,
                            "required": list(properties), "properties": properties}}}}


def _partition_rows(response: dict[str, Any], cursor: int, end: int, final: bool,
                    allowed_prior: set[int]) -> list[dict[str, Any]]:
    rows = copy.deepcopy(response.get('units', []))
    position = cursor
    starts = {row.get('start_index') for row in rows}
    for row in rows:
        left, right = row.get('start_index'), row.get('end_index')
        if (type(left) is not int or type(right) is not int or left != position
                or not left < right <= end or not str(row.get('meaning', '')).strip()):
            raise SubtitlerError(f'Semantic partition expected start_index={position}; got {left}..{right}. '
                                 f'Every supplied atom must occur exactly once, with exclusive end_index <= {end}.')
        deps = row.get('dependency_starts', [])
        if any(type(ref) is not int or ref not in starts | allowed_prior for ref in deps):
            raise SubtitlerError('Semantic unit has an unknown dependency; references must name supplied unit starts')
        row['dependency_starts'] = list(dict.fromkeys(ref for ref in deps if ref != left))
        position = right
    if not rows or final and position != end:
        raise SubtitlerError(f'Semantic partition omitted source tokens: expected exclusive end {end}, got {position}')
    return rows


def _inspect_partition(inspect: Callable[..., dict[str, Any]], cursor: int, end: int, final: bool,
                       allowed_prior: set[int], **request: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persist bounded model corrections; never fill in a missing meaning locally."""
    current = request
    repairs: list[dict[str, Any]] = []
    for attempt in range(3):
        response = inspect(**current)
        try:
            return _partition_rows(response, cursor, end, final, allowed_prior), repairs
        except SubtitlerError as error:
            if attempt == 2:
                raise
            repairs.append({'start_index': cursor, 'end_index': end, 'attempt': attempt + 1,
                            'validation_error': str(error), 'response_revision': content_digest(response)})
            current = copy.deepcopy(request)
            current['evidence']['partition_repair'] = {**repairs[-1], 'previous_response': response}
            current['instruction'] += (' The previous response failed the supplied partition contract. '
                'Repair the index coverage or dependency error using the original atoms; return the complete '
                'corrected partition. Preserve valid meanings and do not discard, invent, or duplicate source text.')
    raise AssertionError('Unreachable partition repair state')


def build_semantic_utterances(document: TranscriptDocument, source_id: str,
                              inspect: Callable[..., dict[str, Any]], identity: dict[str, Any],
                              batch_tokens: int = 400) -> dict[str, Any]:
    """The callback persists each bounded hosted partition through its operation store.

    It receives operation/evidence/instruction/schema keywords; caller owns model,
    reasoning, output allowance and cost. Final-unit overlap avoids forcing a
    semantic break at either a transcription segment or a model batch boundary.
    """
    if batch_tokens < 2:
        raise ValueError("Semantic batches need at least two atoms")
    tokens = _tokens(document, source_id)
    revision = {"version": 3, "source_id": source_id, "source_identity": copy.deepcopy(identity),
                "transcript_revision": document.revision_id, "token_revision": content_digest(tokens)}
    committed: list[dict[str, Any]] = []
    deferred_tails: list[dict[str, int]] = []
    protocol_repairs: list[dict[str, Any]] = []
    cursor, end = 0, min(len(tokens), batch_tokens)
    while cursor < len(tokens):
        rows, repairs = _inspect_partition(inspect, cursor, end, end == len(tokens),
            {row['start_index'] for row in committed}, operation="semantic_utterances", evidence={"identity": revision,
            "tokens": [{key: t[key] for key in ('index', 'text', 'start_ms', 'end_ms', 'alignment')}
                       for t in tokens[cursor:end]], "final_batch": end == len(tokens),
            "prior_units": [{"start_index": row["start_index"], "meaning": row["meaning"]} for row in committed]},
            instruction="Partition these indexed source atoms into complete communicative units, not transcription chunks. "
            "Atoms may be individual characters; never place a unit boundary inside a word, proper name, or expression. "
            "A thought may span a long pause; split distinct propositions where independently intelligible. "
            "Treat transcript segment breaks, newlines and timestamps as acquisition artifacts, never sentence evidence. "
            "Read adjacent atoms as continuous original-language speech before placing a boundary. In Japanese, "
            "a relative clause needs its following head noun, a conditional needs its consequence, and a connective "
            "or unfinished predicate can continue after a long pause. Keep each such grammatical construction together. "
            "Do not isolate clause fragments and replace their missing content with an inferred meaning. "
            "Separate genuinely different completed propositions and link their referential dependencies instead. "
            "The presence of an opaque span or segment forbids splitting INSIDE that atom, but never forbids joining it "
            "to a preceding or following atom required by grammar. Preserve names, numbers, negation, uncertainty "
            "and who is acting. Write meanings in the original language; do not translate or silently correct the text. "
            "If speech is malformed or unclear, describe that uncertainty without inventing an interpretation. "
            "Cover every supplied index exactly once in order with contiguous start_index/end_index ranges (end exclusive). "
            "Do not rewrite spoken text or produce timestamps. Give each unit a concise meaning, at most one short clause. "
            "Opaque spans and segments lack reliable internal word alignment and are indivisible. A unit may include multiple atoms or segments. "
            "dependency_starts lists the start indexes of units needed to understand this unit, not merely related topics. "
            "Dependencies may refer to prior supplied units or earlier/later units in this response; never to unseen text. "
            "Do not attach every unit to its predecessor. No editorial keep/cut preference. The last unit of a nonfinal batch "
            "will be reconsidered with following tokens; do not invent a sentence ending at the batch edge.",
            schema=_schema(cursor, end))
        protocol_repairs.extend(repairs)
        position = rows[-1]['end_index']
        if end == len(tokens):
            committed.extend(rows)
            break
        committed.extend(rows[:-1])
        cursor = rows[-1]["start_index"]
        if position != end:
            # The next batch receives both the provisional last unit and the
            # unassigned suffix from this cursor; neither has been committed.
            deferred_tails.append({'supplied_end_index': end, 'unassigned_start_index': position,
                                   'reconsider_from_index': cursor})
        next_end = min(len(tokens), end + batch_tokens)
        if next_end - cursor > batch_tokens * 4:
            raise SubtitlerError("Semantic thought exceeds bounded context; no artificial break was inserted")
        end = next_end
    units = []
    valid_starts = {row["start_index"] for row in committed}
    for row in committed:
        if any(ref not in valid_starts for ref in row["dependency_starts"]):
            raise SubtitlerError("Semantic dependency no longer addresses a complete unit")
        selected = tokens[row["start_index"]:row["end_index"]]
        units.append({"unit_id": f"{source_id}-utterance-{row['start_index']:05d}", "source_id": source_id,
                      "start_ms": min(t["start_ms"] for t in selected), "end_ms": max(t["end_ms"] for t in selected),
                      "text": "".join(t["text"] for t in selected), "token_ids": [t["token_id"] for t in selected],
                      "token_start_index": row["start_index"], "token_end_index": row["end_index"],
                      "meaning": row["meaning"],
                      "dependency_unit_ids": [f"{source_id}-utterance-{ref:05d}" for ref in row["dependency_starts"]],
                      "alignment": ("opaque_segment" if any(t["alignment"] == "opaque_segment" for t in selected)
                                    else "opaque_span" if any(t["alignment"] == "opaque_span" for t in selected) else "word")})
    return {"schema_version": 1, "identity": revision, "source_id": source_id,
            "deferred_batch_tails": deferred_tails,
            "protocol_repairs": protocol_repairs,
            "duration_ms": round(document.duration_sec * 1000), "tokens": tokens, "units": units,
            "alignment_summary": {"aligned_atoms": sum(t["alignment"] == "word" for t in tokens),
                                  "opaque_segments": sum(t["alignment"] == "opaque_segment" for t in tokens),
                                  "opaque_spans": sum(t["alignment"] == "opaque_span" for t in tokens)},
            "alignment_limitations": [{key: token[key] for key in ("token_id", "start_ms", "end_ms", "alignment_limitation")}
                                     for token in tokens if token["alignment"] != "word"],
            "raw_vad_available": bool(document.backend.raw_vad_speech_intervals),
            "voice_activity": [{"start_ms": round(r.start * 1000), "end_ms": round(r.end * 1000)}
                               for r in document.backend.raw_vad_speech_intervals]}


def semantic_pause_candidates(artifact: dict[str, Any], minimum_pause_ms: int = 2000,
                               leading_margin_ms: int = 200, trailing_margin_ms: int = 300) -> list[dict[str, Any]]:
    """Offer finite internal pause IDs only with word alignment and VAD clearance."""
    if min(minimum_pause_ms, leading_margin_ms, trailing_margin_ms) < 0:
        raise ValueError("Pause and margin durations must be nonnegative")
    if not artifact["raw_vad_available"]:
        return []
    result = []
    for unit in artifact["units"]:
        if unit["alignment"] != "word":
            continue
        tokens = artifact["tokens"][unit["token_start_index"]:unit["token_end_index"]]
        for left, right in zip(tokens, tokens[1:]):
            start, end = left["end_ms"] + trailing_margin_ms, right["start_ms"] - leading_margin_ms
            if (right["start_ms"] - left["end_ms"] < minimum_pause_ms or start >= end
                    or any(row["start_ms"] < end and row["end_ms"] > start for row in artifact["voice_activity"])
                    or any(row["start_ms"] < end and row["end_ms"] > start for row in artifact["tokens"]
                           if row["token_id"] not in (left["token_id"], right["token_id"]))):
                continue
            result.append({"gap_id": f"{unit['unit_id']}-pause-{left['index']}", "unit_id": unit["unit_id"],
                           "start_ms": start, "end_ms": end,
                           "left_token_id": left["token_id"], "right_token_id": right["token_id"]})
    return result


def semantic_handle_bounds(units: list[dict[str, Any]], duration_ms: int,
                           leading_margin_ms: int = 750,
                           trailing_margin_ms: int = 1000) -> dict[str, tuple[int, int]]:
    """Use available inter-unit space for handles; never consume another meaning.

    Original aligned extents remain authoritative. A shared/touching boundary has
    no available handle, while true overlapping extents remain overlapping.
    """
    if min(duration_ms, leading_margin_ms, trailing_margin_ms) < 0:
        raise ValueError("Semantic handle bounds must be nonnegative")
    ordered = sorted(units, key=lambda u: (u['start_ms'], u['end_ms'], u['unit_id']))
    result: dict[str, tuple[int, int]] = {}
    previous_end = 0
    for index, unit in enumerate(ordered):
        start, end = unit['start_ms'], unit['end_ms']
        if not 0 <= start < end <= duration_ms or unit['unit_id'] in result:
            raise SubtitlerError('Invalid or duplicate semantic unit bounds')
        left_space = max(0, start - previous_end)
        next_start = ordered[index + 1]['start_ms'] if index + 1 < len(ordered) else duration_ms
        right_space = max(0, next_start - end)
        left_handle = min(leading_margin_ms, left_space // 2 if index else left_space)
        right_handle = min(trailing_margin_ms, right_space // 2 if index + 1 < len(ordered) else right_space)
        result[unit['unit_id']] = (start - left_handle, end + right_handle)
        previous_end = max(previous_end, end)
    return result


def semantic_cut_rejections(cuts: list[dict[str, Any]], artifact: dict[str, Any],
                            minimum_pause_ms: int = 2000, leading_margin_ms: int = 200,
                            trailing_margin_ms: int = 300) -> dict[str, str]:
    """Reject partial thoughts and removals needed by retained dependent thoughts."""
    gaps = semantic_pause_candidates(artifact, minimum_pause_ms, leading_margin_ms, trailing_margin_ms)
    units, duration = artifact["units"], artifact["duration_ms"]
    handles = semantic_handle_bounds(units, duration, leading_margin_ms, trailing_margin_ms)
    reasons: dict[str, str] = {}
    removed: dict[str, set[str]] = {}
    for index, cut in enumerate(cuts):
        identifier = cut.get("cut_id", f"cut-{index:05d}")
        if identifier in removed:
            raise SubtitlerError("Semantic cut validation requires unique cut IDs")
        removed[identifier] = set()
        start, end = cut["start_ms"], cut["end_ms"]
        if not 0 <= start < end <= duration:
            reasons[identifier] = "Invalid source range"
            continue
        for unit in units:
            left, right = handles[unit['unit_id']]
            if end <= left or start >= right:
                continue
            if start <= left and end >= right:
                removed[identifier].add(unit["unit_id"])
            elif not any(g["unit_id"] == unit["unit_id"] and g["start_ms"] <= start < end <= g["end_ms"] for g in gaps):
                reasons[identifier] = "Removal splits a communicative unit or its natural speech margins"
                break
    while True:
        deleted = set().union(*(refs for identifier, refs in removed.items() if identifier not in reasons))
        needed = {ref for unit in units if unit["unit_id"] not in deleted for ref in unit["dependency_unit_ids"]}
        blocked = {identifier for identifier, refs in removed.items() if identifier not in reasons and refs & needed}
        if not blocked:
            return reasons
        reasons.update({identifier: "Removal deletes a dependency of a retained communicative unit" for identifier in blocked})
