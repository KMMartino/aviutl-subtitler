"""Bounded evidence collection and adaptive cutting over immutable source time.

The same inspection operation collects cheap facts, judges compact passages, and
resolves consequential questions. No narration or timeline rearrangement is made.
"""
from __future__ import annotations

import copy
import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .api_usage import ApiUsageLedger
from .artifact_io import write_json_artifact
from .errors import SubtitlerError
from .editorial_guidance import EDITORIAL_GUIDANCE, project_brief
from .editorial_locale import processing_language_instruction
from .editorial_cutting import VOICE_LEADING_HANDLE_MS, VOICE_TRAILING_HANDLE_MS
from .focused_evidence import plan_focused_evidence
from .editorial_structure import plan_structure, support_rejections
from .semantic_utterances import build_semantic_utterances, semantic_cut_rejections, semantic_pause_candidates
from .operation_store import OperationStore, content_digest
from .speech_gaps import SpeechGapPolicy, find_speech_gaps
from .transcript_document import TranscriptDocument


INSPECTION_VERSION = 6
DECISION_VERSIONS = {"semantic_utterances": 3, "activity_direction": 1, "select_states": 1, "partition_passages": 1, "compare_passages": 11, "judge_passage": 12, "resolve_passage": 12,
                     "review_joins": 14, "review_presentation": 13}
DECISION_TOKENS = 8000
CORE_MS = 300_000
CONTEXT_MS = 30_000


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": {key: copy.deepcopy(value) for key, value in properties.items()},
            "required": list(properties), "additionalProperties": False}


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


TEXT = {"type": "string"}
NUMBER = {"type": "integer"}
FACT_SCHEMA = _object({"summary": TEXT, "facts": _array(_object({
    "start_ms": NUMBER, "end_ms": NUMBER, "observation": TEXT,
    "change": TEXT, "uncertainty": TEXT, "frame_times_ms": _array(NUMBER),
}))})
CUT_SCHEMA = _object({"summary": TEXT, "cuts": _array(_object({
    "start_ms": NUMBER, "end_ms": NUMBER, "reason": TEXT,
    "evidence_ids": _array(TEXT), "requires_retained_ids": _array(TEXT),
})), "question": TEXT, "selection_decisions": _array(_object({
    "passage_id": TEXT, "decision": {"type": "string", "enum": ["follow", "override"]},
    "reason": TEXT, "retained_beats": {**_array(_object({"start_ms": NUMBER, "end_ms": NUMBER,
        "reason": TEXT})), "maxItems": 3},
}))})
JOIN_SCHEMA = _object({"reject_cut_ids": _array(TEXT), "reason": TEXT})
STRATEGY_SCHEMA = _object({"direction": TEXT, "passage_groups": _array(_object({
    "label": TEXT, "relationship": TEXT, "comparison": TEXT, "passages": _array(_object({
        "start_ms": NUMBER, "end_ms": NUMBER, "contribution": TEXT, "omission_cost": TEXT,
        "treatment": {"type": "string", "enum": ["preserve", "condense", "omit"]},
        "edit_instruction": TEXT, "evidence_ids": _array(TEXT),
    })),
})), "priorities": _array(TEXT)})
PRESENTATION_SCHEMA = _object({"decisions": _array(_object({
    "action_id": TEXT, "keep": {"type": "boolean"}, "reason": TEXT,
    "editor_instruction": TEXT, "narrator_direction": TEXT, "related_cut_ids": _array(TEXT),
    "placement_start_ms": NUMBER, "placement_end_ms": NUMBER,
}))})


def transcript_rows(document: TranscriptDocument) -> list[dict[str, Any]]:
    """Use aligned speech chunks, never broad VAD groups as word evidence."""
    from .transcript_normalizer import backend_result_to_aligned_chunks
    return [{"start_ms": round(chunk.chunk.start * 1000), "end_ms": round(chunk.chunk.end * 1000),
             "text": chunk.text} for chunk in backend_result_to_aligned_chunks(document.backend) if chunk.text.strip()]


def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a["start_ms"] < b["end_ms"] and b["start_ms"] < a["end_ms"]


def canonical_visual_facts(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Carry dense observations intact; omission in a later sample is not rebuttal."""
    stage = source.get("stages", {}).get("visual_learning", {})
    segments = stage.get("output", {}).get("segments", [])
    revision = content_digest(segments)
    return [{"id": f"{source['source_id']}-canonical-{index:04d}",
             "start_ms": item["start_ms"], "end_ms": item["end_ms"],
             "observation": item.get("description", item.get("observed_label", "")),
             "change": item.get("observed_label", ""),
             "uncertainty": "Model observation; consult source evidence if contradicted, not merely absent from sparse samples.",
             "evidence_kind": "dense_visual", "provenance": {"stage": "visual_learning", "revision": revision,
                 "segment_index": index - 1, "model": stage.get("output", {}).get("model", "")}}
            for index, item in enumerate(segments, 1)
            if 0 <= item.get("start_ms", -1) < item.get("end_ms", -1) <= source["duration_ms"]]


def speech_facts(source_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Address literal aligned speech just like visual observations, without paraphrasing it."""
    return [{**row, "id": f"{source_id}-speech-{index:04d}", "evidence_kind": "aligned_speech"}
            for index, row in enumerate(rows, 1)]


def strategy_schema(facts: list[dict[str, Any]], duration_ms: int) -> dict[str, Any]:
    schema = copy.deepcopy(STRATEGY_SCHEMA)
    groups = schema["properties"]["passage_groups"]
    groups["maxItems"] = 8
    passages = groups["items"]["properties"]["passages"]
    passages["maxItems"] = 4
    properties = passages["items"]["properties"]
    for key in ("start_ms", "end_ms"):
        properties[key].update(minimum=0, maximum=duration_ms)
    ids = [f["id"] for f in facts]
    # Keep each enum small; large recordings still receive the full inventory
    # and the same consumer validation without exceeding hosted schema limits.
    if 0 < len(ids) <= 1000:
        properties["evidence_ids"]["items"] = {"anyOf": [
            {"type": "string", "enum": ids[start:start + 200]} for start in range(0, len(ids), 200)]}
    return schema


def evidence_context(facts: list[dict[str, Any]], ranges: list[dict[str, Any]],
                     required_ids: set[str] | None = None, margin_ms: int = 15000) -> list[dict[str, Any]]:
    """Retrieve local evidence and explicit distant dependencies without discarding the catalog."""
    required = required_ids or set()
    return [fact for fact in facts if fact["id"] in required or any(
        fact["start_ms"] < row["end_ms"] + margin_ms and fact["end_ms"] > row["start_ms"] - margin_ms
        for row in ranges)]


def assembly_ranges(assembly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Models need the timing map; retrievable fact IDs remain in the persisted assembly."""
    return [{key: value for key, value in row.items() if key != "evidence_ids"} for row in assembly]


def compact_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep observations and reference identities; file provenance remains in the catalog."""
    return [{key: value for key, value in fact.items() if key != "provenance"} for fact in facts]


def collected_interval(item: dict[str, Any], left: int, right: int, stamps: list[int]) -> dict[str, Any]:
    """Represent an explicitly supported instant in a half-open millisecond timeline."""
    result = copy.deepcopy(item)
    if any(stamp not in stamps for stamp in result["frame_times_ms"]):
        raise SubtitlerError("Passage collection cited an unsupplied frame")
    start, end = result["start_ms"], result["end_ms"]
    if start == end and start in result["frame_times_ms"] and left <= start < right:
        result.update(end_ms=start + 1, temporal_scope="sampled_instant", point_timestamp_ms=start)
    if not left <= result["start_ms"] < result["end_ms"] <= right:
        raise SubtitlerError("Passage collection needs an in-range interval or a supported frame instant")
    return result


def related_strategy(strategy: dict[str, Any], ranges: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain whole comparison groups touching this work, including their distant counterparts."""
    return {**strategy, "passage_groups": [group for group in strategy["passage_groups"]
        if any(overlaps(passage, span) for passage in group["passages"] for span in ranges)]}


def join_frame_times(batch: list[dict[str, Any]], assembly: list[dict[str, Any]],
                     focused: list[dict[str, Any]]) -> list[int]:
    """Show actual retained neighbors and one event-targeted interior per removal."""
    times = set()
    for cut in batch:
        before = [span for span in assembly if span["source_end_ms"] <= cut["start_ms"]]
        after = [span for span in assembly if span["source_start_ms"] >= cut["end_ms"]]
        if before:
            span = before[-1]
            times.add(span["source_end_ms"] - min(1500, max(1, (span["source_end_ms"] - span["source_start_ms"]) // 2)))
        if after:
            span = after[0]
            times.add(span["source_start_ms"] + min(1500, (span["source_end_ms"] - span["source_start_ms"]) // 2))
        middle = (cut["start_ms"] + cut["end_ms"]) // 2
        anchors = [row["timestamp_ms"] for row in focused if cut["start_ms"] <= row["timestamp_ms"] < cut["end_ms"]]
        times.add(min(anchors, key=lambda stamp: abs(stamp - middle)) if anchors else middle)
    return sorted(times)


def edit_boundary_choices(core: tuple[int, int], facts: list[dict[str, Any]],
                          speech: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Offer source-timed edit points with retained-speech handles already calculated."""
    left, right = core
    edges = {left, right, *(f[key] for f in facts for key in ("start_ms", "end_ms"))}
    starts = edges | {r["start_ms"] for r in speech} | {r["end_ms"] + VOICE_TRAILING_HANDLE_MS for r in speech}
    ends = edges | {r["end_ms"] for r in speech} | {r["start_ms"] - VOICE_LEADING_HANDLE_MS for r in speech}
    def inside_speech(point: int) -> bool:
        return any(row["start_ms"] < point < row["end_ms"] for row in speech)
    return {
        "start_ms": sorted(t for t in starts if left <= t < right and not inside_speech(t)
            and not any(0 <= t - r["end_ms"] < VOICE_TRAILING_HANDLE_MS for r in speech)),
        "end_ms": sorted(t for t in ends if left < t <= right and not inside_speech(t)
            and not any(0 <= r["start_ms"] - t < VOICE_LEADING_HANDLE_MS for r in speech)),
    }


def selection_contract(strategy: dict[str, Any], source_id: str) -> dict[str, Any]:
    result = copy.deepcopy(strategy)
    index = 0
    for group in result["passage_groups"]:
        for passage in group["passages"]:
            index += 1
            passage["passage_id"] = f"{source_id}-selection-{index:03d}"
    return result


def validate_selection_decisions(decision: dict[str, Any], requirements: list[dict[str, Any]]) -> None:
    expected = {row["passage_id"]: row for row in requirements}
    rows = decision["selection_decisions"]
    if len(rows) != len(expected) or {row["passage_id"] for row in rows} != set(expected):
        raise SubtitlerError("Local editing must account for each assigned selection exactly once")
    for row in rows:
        bounds = expected[row["passage_id"]]
        if not row["reason"].strip() or any(not bounds["start_ms"] <= beat["start_ms"] < beat["end_ms"] <= bounds["end_ms"]
                for beat in row["retained_beats"]):
            raise SubtitlerError("Selection realization has invalid retained beats or no justification")


def selection_outcomes(selections: list[dict[str, Any]], decisions: list[dict[str, Any]],
                       cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for selection in selections:
        realized = [row for decision in decisions for row in decision["selection_decisions"]
                    if row["passage_id"] == selection["passage_id"]]
        removed = sum(max(0, min(selection["end_ms"], cut["end_ms"]) - max(selection["start_ms"], cut["start_ms"])) for cut in cuts)
        result.append({"passage_id": selection["passage_id"], "treatment": selection["treatment"],
            "start_ms": selection["start_ms"], "end_ms": selection["end_ms"], "removed_ms": removed,
            "realizations": realized,
            "unrealized_compression": selection["treatment"] in ("condense", "omit") and removed == 0
                and any(row["decision"] == "follow" for row in realized),
            "removed_retained_beats": [beat for row in realized for beat in row["retained_beats"]
                                      if any(overlaps(beat, cut) for cut in cuts)]})
    return result


def retained_sequence(source_id: str, duration_ms: int, cuts: list[dict[str, Any]],
                      facts: list[dict[str, Any]], timeline_offset_ms: int = 0) -> list[dict[str, Any]]:
    """Map the rough assembly back to source evidence without asking a model to do arithmetic."""
    result = []
    cursor, timeline = 0, timeline_offset_ms
    for cut in [*sorted(cuts, key=lambda c: c["start_ms"]), {"start_ms": duration_ms, "end_ms": duration_ms}]:
        if cursor < cut["start_ms"]:
            span = {"start_ms": cursor, "end_ms": cut["start_ms"]}
            length = span["end_ms"] - cursor
            result.append({"source_id": source_id, "source_start_ms": cursor, "source_end_ms": span["end_ms"],
                "timeline_start_ms": timeline, "timeline_end_ms": timeline + length,
                "evidence_ids": [f["id"] for f in facts if overlaps(span, f)]})
            timeline += length
        cursor = cut["end_ms"]
    return result


def covers(cut: dict[str, Any], facts: list[dict[str, Any]]) -> bool:
    cursor = cut["start_ms"]
    for fact in sorted(facts, key=lambda item: item["start_ms"]):
        if fact["start_ms"] > cursor:
            break
        cursor = max(cursor, fact["end_ms"])
        if cursor >= cut["end_ms"]:
            return True
    return False


def retained_speech_safe(cut: dict[str, Any], speech: list[dict[str, Any]], duration_ms: int) -> bool:
    retained = [(row["start_ms"] / 1000, row["end_ms"] / 1000) for row in speech
                if not cut["start_ms"] <= row["start_ms"] < row["end_ms"] <= cut["end_ms"]]
    gaps = find_speech_gaps(retained, SpeechGapPolicy(
        VOICE_LEADING_HANDLE_MS / 1000, VOICE_TRAILING_HANDLE_MS / 1000, 0, .5, include_edges=True),
        duration=duration_ms / 1000)
    return any(round(gap.cut_start * 1000) <= cut["start_ms"] < cut["end_ms"] <= round(gap.cut_end * 1000)
               for gap in gaps)


def unexplained_voice(document: TranscriptDocument) -> list[dict[str, int]]:
    words = sorted((round(t.start * 1000) - 100, round(t.end * 1000) + 100)
                   for t in document.aligned_tokens() if t.text.strip())
    residual = []
    for interval in document.backend.raw_vad_speech_intervals or document.backend.speech_regions:
        cursor, end = round(interval.start * 1000), round(interval.end * 1000)
        for left, right in words:
            if right <= cursor:
                continue
            if left >= end:
                break
            if left - cursor >= 700:
                residual.append({"start_ms": cursor, "end_ms": min(left, end)})
            cursor = max(cursor, right)
        if end - cursor >= 700:
            residual.append({"start_ms": cursor, "end_ms": end})
    return residual


def compile_cuts(proposals: list[dict[str, Any]], facts: list[dict[str, Any]],
                 speech: list[dict[str, Any]], unresolved_voice: list[dict[str, Any]],
                 duration_ms: int, *, semantic_artifact: dict[str, Any] | None = None
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reject unsafe boundaries and broken references; never invent replacement cuts."""
    fact_by_id = {f["id"]: f for f in facts}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, proposal in enumerate(proposals):
        cut = {**proposal, "cut_id": f"adaptive-{index + 1:05d}", "candidate_kind": "adaptive"}
        left, right = cut["start_ms"], cut["end_ms"]
        reason = ""
        if not 0 <= left < right <= duration_ms or right - left < 500:
            reason = "Invalid or sub-half-second range"
        elif not cut.get("reason", "").strip() or not cut.get("evidence_ids"):
            reason = "No concrete reason or evidence"
        elif any(ref not in fact_by_id for ref in [*cut["evidence_ids"], *cut["requires_retained_ids"]]):
            reason = "Unknown evidence reference"
        elif not covers(cut, [fact_by_id[ref] for ref in cut["evidence_ids"]]):
            reason = "Evidence does not cover the proposed removal"
        elif any(row["start_ms"] < left < row["end_ms"] or row["start_ms"] < right < row["end_ms"] for row in speech):
            reason = "Boundary splits an aligned utterance"
        elif semantic_artifact is None and not retained_speech_safe(cut, speech, duration_ms):
            reason = "Boundary lacks retained speech handles"
        elif any(overlaps(cut, row) for row in unresolved_voice):
            reason = "Unexplained vocal activity needs audio inspection"
        elif any(overlaps(cut, prior) for prior in accepted):
            reason = "Overlapping removal"
        if reason:
            rejected.append({**cut, "rejection": reason})
        else:
            accepted.append(cut)
    # Reject every dependent cut whose retained anchor would itself be removed.
    # This is intentionally conservative and order independent.
    broken = {cut["cut_id"] for cut in accepted if any(
        overlaps(other, fact_by_id[ref]) for ref in cut["requires_retained_ids"] for other in accepted)}
    for cut in accepted:
        if cut["cut_id"] in broken:
            rejected.append({**cut, "rejection": "Required retained evidence would be cut"})
    accepted = sorted([c for c in accepted if c["cut_id"] not in broken], key=lambda c: c["start_ms"])
    # A valid individual boundary can leave only a speech handle between adjacent cuts.
    # Restore the shorter neighboring removal instead of inventing an unreviewed merged cut.
    while accepted:
        failures = semantic_cut_rejections(accepted, semantic_artifact) if semantic_artifact is not None else {}
        if failures:
            rejected.extend({**cut, 'rejection': failures[cut['cut_id']]}
                            for cut in accepted if cut['cut_id'] in failures)
            accepted = [cut for cut in accepted if cut['cut_id'] not in failures]
            continue
        island = next(((left, right) for left, right in zip([None, *accepted], [*accepted, None])
            if 0 < (right["start_ms"] if right else duration_ms) - (left["end_ms"] if left else 0) < 500), None)
        if island is None:
            break
        restore = min((cut for cut in island if cut is not None),
                      key=lambda cut: (cut["end_ms"] - cut["start_ms"], cut["start_ms"]))
        accepted.remove(restore)
        rejected.append({**restore, "rejection": "Adjacent cuts leave a sub-half-second retained fragment"})
    return accepted, rejected


def run_adaptive_cutting(*, project: dict[str, Any], documents: dict[str, TranscriptDocument],
                         baseline: dict[str, Any], workspace: Path, settings: dict[str, Any],
                         usage: ApiUsageLedger, provider: Any = None, frame_extractor: Any = None,
                         artifact_workspace: Path | None = None, motion_analyzer: Any = None) -> dict[str, Any]:
    from .hosted_inspection import HostedInspectionProvider, extract_inspection_frames
    from .frame_motion import sampled_regional_motion
    directory = workspace / "adaptive"
    directory.mkdir(parents=True, exist_ok=True)
    paid_directory = artifact_workspace / project["project_id"] if artifact_workspace is not None else directory
    store = OperationStore(paid_directory / "operations", project["project_id"], usage)
    provider = provider or HostedInspectionProvider(usage, paid_directory / "requests")
    extract = frame_extractor or extract_inspection_frames
    measure_motion = motion_analyzer or sampled_regional_motion
    collection_model = str(settings.get("collection_model", "gpt-5.6-luna"))
    judge_model = str(settings.get("cutting_model", "gpt-5.6-terra"))
    all_cuts: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    intent = project_brief(project)
    director_model = str(settings.get("director_model", judge_model))
    director_effort = str(settings.get("director_reasoning_effort", "low"))
    previous_summaries: list[str] = []
    timeline_offset = 0

    def inspect(task: str, model: str, evidence: dict[str, Any], instruction: str,
                schema: dict[str, Any], images: list[Path], tokens: int, effort: str = "medium") -> dict[str, Any]:
        if task != "semantic_utterances":
            fields = ('editor_instruction', 'narrator_direction') if task == 'review_presentation' else ()
            instruction += "\n" + processing_language_instruction(project, fields)
        inputs = {"model": model, "effort": effort, "evidence": evidence,
                  "instruction": instruction, "schema": schema, "max_output_tokens": tokens,
                  "images": [str(p.resolve()) for p in images]}
        prompt = instruction + "\nAll times are original-source milliseconds.\n" + json.dumps(evidence, ensure_ascii=False)
        return store.execute(task, INSPECTION_VERSION if task == "collect_passage" else DECISION_VERSIONS[task], inputs,
            lambda: provider.inspect(operation=task, model=model, prompt=prompt, schema=schema,
                                     images=images, max_output_tokens=tokens, reasoning_effort=effort),
            lambda value: value, output_files=lambda _: images)

    for source in sorted(project["sources"], key=lambda s: s["order"]):
        sid = source["source_id"]
        document = documents[sid]
        duration = int(source["duration_ms"])
        residual = unexplained_voice(document)
        identity = {"source_id": sid, "transcript_revision": document.revision_id,
                    "fingerprint": document.source_fingerprint, "visual_fingerprint": source.get("visual_fingerprint"),
                    "audio_track": document.audio_track, "game_audio_track": settings.get("game_audio_track"),
                    "frame_sampling": {"interval_ms": 6000, "maximum_edge": 1920, "version": 2}}
        utterances = build_semantic_utterances(document, sid,
            lambda **request: inspect(request['operation'], judge_model,
                {**identity, **request['evidence']}, request['instruction'], request['schema'], [], 16000, 'medium'), identity)
        write_json_artifact(directory / f'{sid}-utterances.json', utterances)
        # Provider segments can span minutes. Supply source-exact complete meanings
        # to each consumer instead of treating those transport groups as speech events.
        speech = [{key: unit[key] for key in ('start_ms', 'end_ms', 'text')}
                  for unit in utterances['units']]
        frames_dir = paid_directory / "frames" / content_digest(identity)[:20]
        focused_packets: list[dict[str, Any]] = []

        def focus(decision: dict[str, Any], core: tuple[int, int], catalog: list[dict[str, Any]],
                  refs: set[str], role: str, maximum: int,
                  retained: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            plan = plan_focused_evidence(source_id=sid, duration_ms=duration, core=core, decision=decision,
                facts=catalog, source_identity=identity, treatment_refs=tuple(sorted(refs)), max_frames=maximum)
            if role == "join":
                plan["frames"] = [row for row in plan["frames"] if any(
                    request["kind"] == "anchor" for request in row["requests"])]
                stamps = join_frame_times(decision["cuts"], retained or [], plan["frames"])
                unused = [row for row in plan["frames"] if row["timestamp_ms"] not in stamps]
                plan["frames"] = [row for row in plan["frames"] if row["timestamp_ms"] in stamps]
                served = {ref for row in plan["frames"] for request in row["requests"] for ref in request["evidence_ids"]}
                plan["unserved_anchor_ids"] = sorted(set(plan["unserved_anchor_ids"]) | {
                    ref for row in unused for request in row["requests"] for ref in request["evidence_ids"] if ref not in served})
                plan["join_frame_times_ms"] = stamps
            inputs = {"plan": plan, "role": role}
            def collect() -> dict[str, Any]:
                paths = extract(Path(source["visual_path"]), [row["timestamp_ms"] for row in plan["frames"]], frames_dir)
                return {**plan, "role": role, "frame_paths": [str(path.resolve()) for path in paths]}
            packet = store.execute("focused_evidence", 2, inputs, collect, lambda value: value,
                output_files=lambda value: [Path(path) for path in value["frame_paths"]])
            focused_packets.append(packet)
            return packet
        passages: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        motion_observations = []
        for start in range(0, duration, CORE_MS):
            end = min(duration, start + CORE_MS)
            left, right = max(0, start - CONTEXT_MS), min(duration, end + CONTEXT_MS)
            stamps = sorted(set([*range(left, right, 6_000), max(left, right - 100)]))
            images = extract(Path(source["visual_path"]), stamps, frames_dir)
            motion = store.execute("sample_motion", 1,
                {**identity, "frame_times_ms": stamps, "images": [str(p.resolve()) for p in images]},
                lambda: measure_motion(images, stamps), lambda value: value, output_files=lambda _: images)
            motion_observations.extend(motion)
            evidence = {**identity, "intent": {key: project.get(key) for key in
                        ("title_or_game", "objective", "must_keep_notes", "de_emphasize_notes")},
                        "core": [start, end], "frame_times_ms": stamps,
                        "regional_motion": motion,
                        "speech": [r for r in speech if r["start_ms"] < right and r["end_ms"] > left],
                        "unexplained_voice": [r for r in residual if r["start_ms"] < right and r["end_ms"] > left]}
            print(f"Adaptive evidence {sid}: {start / 60000:.1f}-{end / 60000:.1f} min", flush=True)
            collection_schema = copy.deepcopy(FACT_SCHEMA)
            collection_schema["properties"]["facts"]["maxItems"] = 40
            collection_schema["properties"]["facts"]["items"]["properties"]["frame_times_ms"].update(
                maxItems=3, items={**NUMBER, "enum": stamps})
            for key in ('start_ms', 'end_ms'):
                collection_schema['properties']['facts']['items']['properties'][key].update(minimum=left, maximum=right)
            collected = inspect("collect_passage", collection_model, evidence,
                "Describe observable activity, meaningful changes, repetition and uncertainty throughout the core. "
                "Do not recommend edits or infer victory/failure from generic transitions. Record quiet discoveries, "
                "readable UI facts and visible reactions. Adjacent images are labeled by frame_times_ms in order. "
                "Speech is exact evidence; unexplained voice is NOT silence or classified laughter. "
                "Regional-motion cells are row-major on the supplied grid: dominant unchanged regions with a moving corner "
                "may show gameplay capture frozen while facecam continues, but menus or intentional stillness can also explain it. "
                "Use images and speech to distinguish these. When a freeze is acknowledged late, trace the unchanged gameplay "
                "back to the earlier sampled evidence; record observed stillness and acknowledgement as distinct times. "
                "Sampling bounds are not exact onset. Do not infer unrecorded gameplay actions from continuing commentary "
                "or infer completion from a freeze. Use at most 40 concise factual intervals; each start/end must lie in the core. "
                "Preserve brief changes within longer activity instead of smoothing them into a generic summary. "
                "For visual claims, frame_times_ms names up to three supplied frames that actually support the observation; "
                "empty is valid for speech-only evidence or unsampled events. Do not describe every frame. "
                "Never invent unreadable details.",
                collection_schema, images, 8000)
            local_facts: list[dict[str, Any]] = []
            for item in collected["facts"]:
                # Context observations are valid source evidence, including events
                # crossing a processing boundary. Only executable cuts are core-bound.
                item = collected_interval(item, left, right, stamps)
                local_facts.append({**item, "evidence_kind": "sampled_visual", "id": f"{sid}-fact-{len(facts) + len(local_facts) + 1:04d}"})
            if not local_facts:
                raise SubtitlerError("Passage collection returned no factual coverage")
            facts.extend(local_facts)
            passages.append({"start_ms": start, "end_ms": end, "summary": collected["summary"],
                             "facts": local_facts, "speech": evidence["speech"], "images": images, "frame_times_ms": stamps})
        dense_facts = canonical_visual_facts(source)
        literal_speech = speech_facts(sid, speech)
        facts = store.execute("evidence_catalog", 2,
            {"sampled": facts, "dense": dense_facts, "speech": literal_speech,
             "transcript_revision": document.revision_id, "audio_track": document.audio_track},
            lambda: [*facts, *dense_facts, *literal_speech], lambda value: value)
        for digest, observation in {content_digest(item): item for item in motion_observations}.items():
            facts.append({"id": f"{sid}-motion-{digest[:12]}", "evidence_kind": "sampled_motion",
                "start_ms": observation["start_ms"], "end_ms": observation["end_ms"],
                "observation": json.dumps(observation, ensure_ascii=False),
                "change": "Measured stability of image regions between the compared samples.",
                "uncertainty": "Samples do not establish exact onset or continuous absence of activity; menus and intentional stillness remain possible."})
        pauses = semantic_pause_candidates(utterances) if settings.get('trim_utterance_pauses', False) else []
        structured = plan_structure(source, utterances, facts, inspect, identity, intent, EDITORIAL_GUIDANCE,
                                    director_model, director_effort, judge_model, pauses)
        write_json_artifact(directory / f'{sid}-structure.json', structured)
        strategy = structured['selection_strategy']
        selections = [row for group in strategy['passage_groups'] for row in group['passages']]
        semantic_passages, work_packets = structured['semantic_passages'], structured['work_packets']
        decisions = structured['decisions']
        aligned_atoms = [{'start_ms': t['start_ms'], 'end_ms': t['end_ms']} for t in utterances['tokens']]
        cuts, rejected = compile_cuts(structured['proposals'], facts, aligned_atoms, residual, duration,
                                     semantic_artifact=utterances)

        def protect_meanings(current: list[dict[str, Any]]) -> list[dict[str, Any]]:
            while True:
                failures = semantic_cut_rejections(current, utterances)
                failures.update(support_rejections(current, utterances['units'], structured['structure'], decisions))
                if not failures:
                    return current
                rejected.extend({**c, 'rejection': failures[c['cut_id']]} for c in current if c['cut_id'] in failures)
                current = [c for c in current if c['cut_id'] not in failures]
        cuts = protect_meanings(cuts)
        assembly = retained_sequence(sid, duration, cuts, facts, timeline_offset)
        # Review bounded batches of joins after compilation, with all accepted cuts
        # visible so the judge can detect interacting deletions.
        for offset in range(0, len(cuts), 8):
            batch = cuts[offset:offset + 8]
            batch_strategy = related_strategy(strategy, batch)
            batch_refs = {ref for group in batch_strategy["passage_groups"] for row in group["passages"] for ref in row["evidence_ids"]}
            packet = focus({"question": "Does the removal lose the cited event or its required retained context?", "cuts": batch},
                (max(0, min(c["start_ms"] for c in batch) - 1500), min(duration, max(c["end_ms"] for c in batch) + 1500)),
                facts, batch_refs, "join", 8, assembly)
            stamps = packet["join_frame_times_ms"]
            images = extract(Path(source["visual_path"]), stamps, frames_dir)
            join = inspect("review_joins", judge_model,
                {**identity, "intent": intent, "editorial_guidance": EDITORIAL_GUIDANCE,
                 "selection_strategy": batch_strategy,
                 "cuts": [{key: c[key] for key in ("cut_id", "start_ms", "end_ms")} for c in cuts], "check": batch,
                 "facts": compact_facts(evidence_context(facts, batch, batch_refs | {ref for c in batch
                     for ref in [*c["evidence_ids"], *c["requires_retained_ids"]]})),
                 "retained_sequence": assembly_ranges(assembly),
                 "semantic_utterances": [u for u in utterances["units"] if any(u["start_ms"] < c["end_ms"]+15000 and u["end_ms"] > c["start_ms"]-15000 for c in batch)],
                 "focused_evidence": {key: packet[key] for key in ("frames", "limitations", "unserved_anchor_ids")},
                 "speech": [r for r in speech if any(r["start_ms"] < c["end_ms"] + 15000 and
                             r["end_ms"] > c["start_ms"] - 15000 for c in batch)], "frame_times_ms": stamps},
                "Independently verify each removal's factual premise using its timestamped interior image and literal "
                "speech. Compacted facts can be mistaken: original images and speech take precedence. Reject a cut "
                "whose premise contradicts this evidence (for example combat mislabeled as a save menu). "
                "An interior sample cannot disprove a brief event elsewhere in the interval. Focused frames reference "
                "the observations they seek; some observations remain unserved. Midpoints are unverified search locations: "
                "inspect the image instead of assuming it shows the described event. If an added frame changes a verdict, "
                "name its timestamp, the cut ID, and what it establishes in reason. A meaningful event is not automatically "
                "worth retaining; assess what its omission loses beyond the surviving sequence. Dense observations retain "
                "their evidence status when absent from sparse samples; distinguish omission from contradiction. "
                "Read the assembled utterances across neighboring cuts as connected language. Check referents, "
                "unfinished clauses across pauses, and a plan without its required execution/result. "
                "Also check the retained sequence at these proposed joins. Reject concrete broken thought, lost "
                "required context, unreadable discovery, or jarring state discontinuity. Do not veto merely because "
                "a removal contains activity. Distinguish discontinuity already present in the source from context lost "
                "because of this cut: retaining frozen footage cannot restore a recovery or transition never recorded. "
                "Apply this project's intended experience when assessing losses of rhythm, emotion or progression. "
                "Reject only when the removed evidence would actually resolve the problem. "
                "Return IDs only from check. Never invent replacement ranges.",
                JOIN_SCHEMA, images, DECISION_TOKENS)
            ids = {c["cut_id"] for c in batch}
            if any(cid not in ids for cid in join["reject_cut_ids"]):
                raise SubtitlerError("Join review referenced an unknown cut")
            rejected.extend({**c, "rejection": join["reason"]} for c in batch if c["cut_id"] in join["reject_cut_ids"])
        rejected_ids = {c["cut_id"] for c in rejected}
        cuts = protect_meanings([c for c in cuts if c["cut_id"] not in rejected_ids])
        assembly = store.execute("assemble_selection", 1,
            {"source_id": sid, "duration_ms": duration, "cuts": cuts, "facts": facts, "timeline_offset_ms": timeline_offset},
            lambda: retained_sequence(sid, duration, cuts, facts, timeline_offset), lambda value: value)
        outcomes = store.execute("selection_outcomes", 1,
            {"selections": selections, "decisions": decisions, "cuts": cuts},
            lambda: selection_outcomes(selections, decisions, cuts), lambda value: value)
        for outcome in outcomes:
            outcome['unrealized_compression'] = outcome['treatment'] in ('condense', 'omit') and outcome['removed_ms'] == 0
            outcome['realizations'] = [{'decision': 'measured', 'reason':
                f"{outcome['removed_ms']/1000:.2f}s removed after state selection and meaning protection.",
                'retained_beats': []}]
        timeline_offset += duration - sum(c["end_ms"] - c["start_ms"] for c in cuts)
        all_cuts.extend({**c, "cut_id": f"{sid}-{c['cut_id']}", "source_id": sid,
                         "internal_reason": c["reason"]} for c in cuts)
        source_reports.append({"source_id": sid, "identity": identity, "path": source["visual_path"], "duration_ms": duration,
                               "facts": facts, "selection_strategy": strategy, "decisions": decisions, "rejected": rejected,
                               "unexplained_voice": residual, "motion_observations": motion_observations,
                               "retained_sequence": assembly, "selection_outcomes": outcomes,
                               "semantic_passages": semantic_passages, "work_packets": work_packets,
                               "semantic_utterances": utterances, "activity_structure": structured,
                               "focused_evidence_packets": focused_packets, "cuts": cuts})
        previous_summaries.extend(p["summary"] for p in passages)
    final_actions = copy.deepcopy(baseline.get("final_actions", []))
    presentation: dict[str, Any] = {"decisions": []}
    if final_actions:
        presentation_sources = []
        for source in source_reports:
            drafts = [action for action in final_actions if action["source_id"] == source["source_id"]]
            context_ranges = [{"start_ms": max(0, action["start_ms"] - 30000),
                               "end_ms": min(source["duration_ms"], action["end_ms"] + 30000)} for action in drafts]
            context_strategy = related_strategy(source["selection_strategy"], context_ranges)
            refs = {ref for group in context_strategy["passage_groups"] for row in group["passages"] for ref in row["evidence_ids"]}
            presentation_sources.append({"source_id": source["source_id"], "duration_ms": source["duration_ms"],
                "placement_context_ranges": context_ranges,
                "facts": compact_facts(evidence_context(source["facts"], context_ranges, refs, 0)),
                "selection_strategy": context_strategy,
                "retained_sequence": assembly_ranges(source["retained_sequence"]),
                "speech_near_drafts": [row for row in transcript_rows(documents[source["source_id"]])
                                       if any(overlaps(row, span) for span in context_ranges)]})
        presentation = inspect("review_presentation", director_model,
            {"intent": intent, "editorial_guidance": EDITORIAL_GUIDANCE, "cuts": all_cuts,
             "draft_actions": final_actions, "sources": presentation_sources},
            "Review provisional narration against the actual proposed cut, like an editor checking an assembly. "
            "Return exactly one decision for each draft action. Decide whether it still adds value after these cuts, "
            "or whether retained source speech/presentation already does the job. A suggested compression that did not "
            "happen cannot justify narration claiming that it did. No mandatory narration count or format. "
            "The retained_sequence explicitly maps the assembled timeline to original source ranges. Use that map to "
            "check what the viewer still sees and hears; factual availability in the source is not the same as survival "
            "in the assembly. Likewise, a source gap is not necessarily a gap created by the edit. "
            "For a kept draft, rewrite concise directly recordable narrator wording and a separate editor instruction "
            "with placement relative to the retained material. Do not invent recorded narration or exact speaking duration. "
            "For kept drafts, placement_start_ms and placement_end_ms define a suggested source-time placement window "
            "entirely inside one retained source range of that draft's source. You may move it from the provisional "
            "window; make the editor instruction agree with it. This is an anchor window, not measured voiceover duration. "
            "Keep placement within one supplied placement_context_ranges interval, where literal speech is available. "
            "For omitted drafts set both placement fields to zero. "
            "Use related_cut_ids only for actual supporting cuts; empty is valid for a useful independent explanation. "
            "Explain the decision to the editor in reason. Use source observations and literal speech as evidence; "
            "an omitted event in sparse sampling does not overturn explicit denser observations. "
            "Analyst limitations are not a viewer script. Respect the creator's intended perspective and knowledge "
            "at the time; no invented outcomes or motives. You may omit or revise drafts, not invent new source facts.",
            PRESENTATION_SCHEMA, [], 6000, director_effort)
        by_id = {action["action_id"]: action for action in final_actions}
        decisions = presentation["decisions"]
        if len(decisions) != len(by_id) or {d["action_id"] for d in decisions} != set(by_id):
            raise SubtitlerError("Presentation review must address each draft exactly once")
        cut_ids = {c["cut_id"] for c in all_cuts}
        selected = []
        for decision in decisions:
            if any(cid not in cut_ids for cid in decision["related_cut_ids"]):
                raise SubtitlerError("Presentation review referenced an unknown cut")
            if decision["keep"]:
                if not decision["editor_instruction"].strip() or not decision["narrator_direction"].strip():
                    raise SubtitlerError("Kept narration needs separate editor and narrator directions")
                action = by_id[decision["action_id"]]
                start, end = decision["placement_start_ms"], decision["placement_end_ms"]
                if not start < end or not any(
                    span["source_start_ms"] <= start < end <= span["source_end_ms"]
                    for source in source_reports if source["source_id"] == action["source_id"]
                    for span in source["retained_sequence"]
                ):
                    raise SubtitlerError("Narration placement must lie within one retained source range")
                if not any(span["start_ms"] <= start < end <= span["end_ms"]
                           for source in presentation_sources if source["source_id"] == action["source_id"]
                           for span in source["placement_context_ranges"]):
                    raise SubtitlerError("Narration placement lacks local speech context")
                action.update(start_ms=start, end_ms=end)
                action["instruction"] = decision["editor_instruction"]
                action["narration_guidance"].update(narrator_direction=decision["narrator_direction"],
                    related_cut_ids=decision["related_cut_ids"], added_value=decision["reason"])
                selected.append(action)
        final_actions = selected
    report = {"schema_version": 6, "workflow": "adaptive_cutting", "sources": source_reports,
              "project_brief": intent, "editorial_guidance": EDITORIAL_GUIDANCE,
              "presentation_review": presentation,
              "baseline_cuts": baseline["confirmed_cuts"], "confirmed_cuts": all_cuts,
              "api_cost_usd": usage.total_cost_usd, "api_usage": [asdict(row) for row in usage.rows]}
    report_path = directory / "review.json"
    write_json_artifact(report_path, report)
    html_path = directory / "review.html"
    write_adaptive_report(html_path, report)
    result = copy.deepcopy(baseline)
    result["final_actions"] = final_actions
    mapping = project.get("editorial_map", {})
    draft_briefs = (mapping.get("global_reconciliation", {}).get("output") or {}).get(
        "narration_briefs", mapping.get("narration_briefs", []))
    result["narration_briefs"] = [{**brief, "editor_instruction": action["instruction"],
                                  "start_ms": action["start_ms"], "end_ms": action["end_ms"],
                                  "narrator_direction": action["narration_guidance"]["narrator_direction"],
                                  "added_value": action["narration_guidance"]["added_value"]}
                                 for action in final_actions for brief in draft_briefs
                                 if brief["id"] in action.get("narration_brief_ids", [])]
    result["plan_audit"] = {**result.get("plan_audit", {}),
                            "summary": "Adaptive markers propose evidence-backed removals of spoken or quiet material; the human editor is authoritative."}
    removed_ms = sum(c["end_ms"] - c["start_ms"] for c in all_cuts)
    result.update(confirmed_cuts=all_cuts, removed_ms=removed_ms,
                  estimated_final_ms=max(0, sum(int(s["duration_ms"]) for s in project["sources"]) - removed_ms),
                  cutting_mode="adaptive", adaptive_report_path=str(html_path), adaptive_artifact_path=str(report_path),
                  baseline_confirmed_cuts=copy.deepcopy(baseline["confirmed_cuts"]))
    return result


def write_adaptive_report(path: Path, report: dict[str, Any]) -> None:
    esc = html.escape
    rows = []
    for source in report["sources"]:
        if source.get('semantic_utterances'):
            rows.append('<details><summary>Complete utterances and meaning dependencies</summary><ul>')
            for unit in source['semantic_utterances']['units']:
                rows.append(f"<li>{unit['start_ms']/1000:.2f}–{unit['end_ms']/1000:.2f}s "
                    f"<b>{esc(unit['unit_id'])}</b>: {esc(unit['text'])}<br>{esc(unit['meaning'])}"
                    f"<br>Requires: {esc(', '.join(unit['dependency_unit_ids']) or 'None')}</li>")
            rows.append('</ul></details>')
        if source.get('activity_structure'):
            rows.append('<details><summary>State and utterance selections</summary><ul>')
            for decision in source['activity_structure']['decisions']:
                for state in decision['states']:
                    rows.append(f"<li>{esc(state['state_id'])}: {esc(state['decision'])} — "
                        f"{esc(state['contribution'])}<br>Transition: {esc(state['transition_reason'])}</li>")
            rows.append('</ul></details>')
        if source.get("semantic_passages"):
            rows.append("<details><summary>Meaningful passages and processing boundaries</summary><p>"
                        "Passages are interpretations of evidence; packet boundaries limit processing, not editorial meaning.</p><ul>")
            rows.extend(f"<li>{row['start_ms']/1000:.2f}–{row['end_ms']/1000:.2f}s: {esc(row['purpose'])}</li>"
                        for row in source["semantic_passages"])
            rows.append("</ul><p>Processing packets: " + "; ".join(
                f"{row['start_ms']/1000:.2f}–{row['end_ms']/1000:.2f}s"
                + (" (long passage split)" if row["split_parent"] else "") for row in source["work_packets"]) + "</p></details>")
        strategy = source.get("selection_strategy", {})
        if strategy:
            rows.append(f"<h2>Editor selection strategy</h2><p>{esc(strategy['direction'])}</p>")
            for group in strategy.get("passage_groups", []):
                rows.append(f"<h3>{esc(group['label'])}</h3><p>{esc(group['relationship'])}: {esc(group['comparison'])}</p><ul>")
                rows.extend(f"<li>{attempt['start_ms']/1000:.2f}–{attempt['end_ms']/1000:.2f}s: "
                            f"{esc(attempt.get('treatment', ''))}: {esc(attempt['edit_instruction'])}<br>Contribution: {esc(attempt['contribution'])}"
                            f"<br>Cost of omission: {esc(attempt['omission_cost'])}</li>" for attempt in group["passages"])
                rows.append("</ul>")
        if source.get("selection_outcomes"):
            rows.append("<details><summary>Selection realization and overrides</summary><ul>")
            for outcome in source["selection_outcomes"]:
                rows.append(f"<li>{outcome['start_ms']/1000:.2f}–{outcome['end_ms']/1000:.2f}s: "
                    f"{esc(outcome['treatment'])}; {outcome['removed_ms']/1000:.2f}s removed. "
                    + " ".join(esc(row["decision"] + ": " + row["reason"]) for row in outcome["realizations"])
                    + (" <b>Planned compression did not survive.</b>" if outcome["unrealized_compression"] else "")
                    + (" <b>A nominated retained beat overlaps a cut; inspect.</b>" if outcome["removed_retained_beats"] else "") + "</li>")
            rows.append("</ul></details>")
        rows.append(f"<h2>{esc(Path(source['path']).name)}</h2><table><tr><th>Source time</th><th>Remove</th><th>Reason</th></tr>")
        for cut in source["cuts"]:
            warnings = "".join(
                f"<br><strong>Listen at {edge['timestamp_ms']/1000:.3f}s:</strong> "
                f"{esc(edge['basis'])}; {esc(edge['voice_evidence'])}. {esc(edge['limitation'])}"
                for edge in cut.get('boundary_provenance', []) if edge.get('review_required'))
            rows.append(f"<tr><td>{cut['start_ms']/1000:.2f}–{cut['end_ms']/1000:.2f}s</td>"
                        f"<td>{(cut['end_ms']-cut['start_ms'])/1000:.2f}s</td><td>{esc(cut['reason'])}{warnings}</td></tr>")
        rows.append("</table><details><summary>Preserved uncertain or unsafe proposals</summary><ul>")
        rows.extend(f"<li>{c['start_ms']/1000:.2f}s: {esc(c['rejection'])}</li>" for c in source["rejected"])
        rows.extend(f"<li>{d['start_ms']/1000:.2f}s: {esc(d['question'])}</li>"
                    for d in source["decisions"] if d["status"] == "unresolved_preserved")
        rows.append("</ul></details>")
    if report.get("presentation_review", {}).get("decisions"):
        rows.append("<h2>Narration checked against this assembly</h2><ul>")
        rows.extend(f"<li>{esc(d['action_id'])}: {'Keep' if d['keep'] else 'Omit'} — {esc(d['reason'])}</li>"
                    for d in report["presentation_review"]["decisions"])
        rows.append("</ul>")
    path.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Adaptive cutting review</title><style>body{background:#111b23;color:#e5edf4;font:17px/1.6 system-ui;'
        'max-width:1100px;margin:auto;padding:28px}table{width:100%;border-collapse:collapse}td,th{padding:12px;'
        'text-align:left;border-bottom:1px solid #456}td{overflow-wrap:anywhere}details{margin:24px 0}</style>'
        f"<h1>Adaptive cutting review</h1><p>{len(report['confirmed_cuts'])} adaptive cuts; "
        f"{len(report['baseline_cuts'])} baseline speech-gap markers. Recorded analysis cost, including previous attempts: ${report['api_cost_usd']:.4f}.</p>"
        '<p>Original media is unchanged. Times refer to the source, not the shortened timeline. '
        'Unexplained vocal activity is preserved pending audio interpretation.</p>' + "".join(rows) + '</html>', encoding="utf-8")
