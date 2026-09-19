"""Reusable observed scenes, keyed by source content and analysis contract."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

from .artifact_io import write_json_artifact
from .media_analysis import BROLL_PROMPT_VERSION, PROMPT_VERSION, MediaAnalysisResult
from .media_identity import fingerprint_source


def evidence_directory(media_path: Path) -> Path:
    root = Path(os.environ.get("SUBUTL_VISUAL_CACHE", str(
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "SubUtl" / "visual-evidence")))
    return root / fingerprint_source(media_path).digest


def publish_visual_evidence(
    media_path: Path, result: MediaAnalysisResult, *, profile: str, stage_version: int,
    start_sec: float, end_sec: float,
) -> None:
    directory = evidence_directory(media_path)
    path = directory / f"{profile}-{start_sec:.3f}-{end_sec:.3f}.json"
    write_json_artifact(path, {
        "contract": 1, "media_identity": asdict(fingerprint_source(media_path)),
        "prompt_version": BROLL_PROMPT_VERSION if profile == "broll" else PROMPT_VERSION, "profile": profile, "stage_version": stage_version,
        "start_sec": start_sec, "end_sec": end_sec, "result": asdict(result),
    })


def load_visual_evidence(
    media_path: Path, *, start_sec: float = 0, end_sec: float | None = None,
    maximum_spacing_sec: float | None = None,
    model: str | None = None,
    profile: str | None = None,
) -> MediaAnalysisResult | None:
    from .editorial_project import EDITORIAL_STAGE_VERSIONS
    from .visual_analysis import _media_analysis_result_from_dict

    if not media_path.is_file():
        return None
    identity = asdict(fingerprint_source(media_path))
    candidates: list[MediaAnalysisResult] = []
    for path in evidence_directory(media_path).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (data.get("contract") != 1 or data.get("media_identity") != identity
                    or data.get("prompt_version") != (BROLL_PROMPT_VERSION if data.get("profile") == "broll" else PROMPT_VERSION)
                    or profile is not None and data.get("profile") != profile
                    or data.get("profile") not in {"editorial", "broll"}):
                continue
            if data["profile"] == "editorial" and data.get("stage_version") != EDITORIAL_STAGE_VERSIONS["visual_learning"]:
                continue
            bound = float(data["end_sec"]) if end_sec is None else end_sec
            if float(data["start_sec"]) > start_sec or float(data["end_sec"]) < bound:
                continue
            result = _media_analysis_result_from_dict(data["result"])
            if result is None or model is not None and result.model != model:
                continue
            segments = [replace(segment, start_ms=max(round(start_sec * 1000), segment.start_ms),
                                end_ms=min(round(bound * 1000), segment.end_ms), suitability="")
                        for segment in result.segments
                        if segment.end_ms > start_sec * 1000 and segment.start_ms < bound * 1000]
            cursor = round(start_sec * 1000)
            for segment in segments:
                if segment.start_ms != cursor or segment.end_ms <= segment.start_ms or not segment.observed_label:
                    break
                if maximum_spacing_sec is not None and (
                    segment.evidence_spacing_sec <= 0 or segment.evidence_spacing_sec > maximum_spacing_sec
                    or segment.handoff_required
                ):
                    break
                cursor = segment.end_ms
            if not segments or cursor != round(bound * 1000):
                continue
            # Reusing observations is free; previous analysis cost remains in its
            # original artifact. Project-specific summaries are not imported.
            candidates.append(replace(result, segments=segments,
                description="; ".join(dict.fromkeys(segment.observed_label for segment in segments))[:4000],
                input_tokens=0, output_tokens=0, cost_usd=0))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda value: max(segment.evidence_spacing_sec for segment in value.segments))
