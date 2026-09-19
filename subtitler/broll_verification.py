"""Verify a finalist against dense, source-timed visual evidence before export."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Sequence

from .broll import (
    BROLL_PLAN_RESPONSE_SCHEMA, BrollNeed, CatalogSegment, ProposedPlacement,
    _planning_prompt, apply_confidence_policy, parse_broll_response,
)
from .media_analysis import OpenAIMediaAnalysisProvider, _sampling_plan, analyze_media
from .models import Subtitle
from .visual_analysis import _media_analysis_result_from_dict
from .visual_evidence_store import load_visual_evidence

if TYPE_CHECKING:
    from .broll_session import BrollSession


def verify_placement(session: BrollSession, item: ProposedPlacement, subtitles: Sequence[Subtitle]) -> ProposedPlacement | None:
    if item.asset.media_kind == "image":
        return item
    end = item.source_end_sec
    if end is None:
        return None
    duration = subtitles[item.end_line - 1].end_time - subtitles[item.start_line - 1].start_time
    if end - item.source_start_sec + 0.05 < duration:
        return None
    end = min(end, item.source_start_sec + duration)
    known = next((segment for segment in item.asset.segments
                  if segment.start_sec <= item.source_start_sec and segment.end_sec >= end), None)
    if known and (known.locked or known.description_source == "user"):
        return replace(item, source_end_sec=end)
    model = str(session.config["broll"]["analysis_model"])
    stat = item.asset.path.stat()

    def produce() -> dict:
        result = load_visual_evidence(item.asset.path, start_sec=item.source_start_sec, end_sec=end,
                                      maximum_spacing_sec=1.0, model=model)
        if result is None:
            result = analyze_media(
                media_path=item.asset.path, media_kind="video", duration_sec=item.asset.duration_sec,
                start_sec=item.source_start_sec, end_sec=end, detail="precise", ffmpeg="ffmpeg",
                sampling_scale=max(1.0, (math.ceil(duration) + 1) / _sampling_plan(duration, "precise").coarse_count),
                provider=OpenAIMediaAnalysisProvider(model), include_frame_differences=False,
            )
            session.usage.add(provider=result.provider, model=result.model, operation="broll_visual_verification",
                              input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                              cost_usd=result.cost_usd)
        return asdict(result)

    raw = session.execute("broll_visual_verification", {
        "path": str(item.asset.path.resolve()), "size": stat.st_size, "modified_ns": stat.st_mtime_ns,
        "start": item.source_start_sec, "end": end, "model": model,
    }, produce, lambda value: value)
    result = _media_analysis_result_from_dict(raw)
    if result is None or not result.segments or any(
        segment.handoff_required or not segment.observed_label or segment.evidence_spacing_sec > 1.0
        for segment in result.segments
    ):
        return None
    segments = tuple(CatalogSegment(
        id=f"verified-{index}", start_sec=segment.start_ms / 1000, end_sec=segment.end_ms / 1000,
        description=segment.description, observed_label=segment.observed_label,
        confidence=segment.confidence, tags=tuple(segment.tags), visual_category=segment.visual_category,
        suitability=segment.suitability, evidence_spacing_sec=segment.evidence_spacing_sec,
        handoff_reason=segment.handoff_reason,
    ) for index, segment in enumerate(result.segments))
    asset = replace(item.asset, segments=segments, description=result.description, description_source="ai")
    need = BrollNeed(item.start_line, item.end_line, item.reason, (), "video", item.need_score)
    prompt = _planning_prompt(subtitles[item.start_line - 1:item.end_line], [asset], [need],
                              line_offset=item.start_line - 1)
    prompt += ("\nFINAL VERIFICATION: Use these densely sampled observations to decide whether the exact requested "
               "action is visibly established. Return no placement if uncertain. Do not expand the transcript range. "
               "The chosen source interval must cover the entire target passage. Original proposal: "
               + json.dumps({"start_line": item.start_line, "end_line": item.end_line, "reason": item.reason}))
    response = session.complete(prompt, operation="broll_verify_placement", response_schema=BROLL_PLAN_RESPONSE_SCHEMA)
    proposed, _, _ = parse_broll_response(response, [asset], subtitles)
    accepted, _ = apply_confidence_policy(proposed)
    return next((replace(candidate, id=item.id) for candidate in accepted
                 if candidate.start_line == item.start_line and candidate.end_line == item.end_line
                 and candidate.source_end_sec is not None
                 and candidate.source_end_sec - candidate.source_start_sec + 0.05 >= duration), None)
