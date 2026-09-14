"""Plan bounded source-frame retrieval for a particular editorial decision."""

from __future__ import annotations

from typing import Any

from .operation_store import content_digest


def plan_focused_evidence(*, source_id: str, duration_ms: int, core: tuple[int, int],
                          decision: dict[str, Any], facts: list[dict[str, Any]],
                          source_identity: dict[str, Any],
                          treatment_refs: tuple[str, ...] = (), max_frames: int = 16) -> dict[str, Any]:
    """Return a persistent plan; callers extract its exact, chronological timestamps.

    Optional fact.frame_times_ms must identify actual supporting observations, not
    every frame available in the source. Without it, a midpoint is only a search
    location. Neither an image nor a midpoint proves continuous interval coverage.
    """
    if not 0 <= max_frames <= 16:
        raise ValueError("Focused evidence allows between zero and sixteen frames")
    start, end = max(0, core[0]), min(duration_ms, core[1])
    cuts = sorted((c for c in decision.get("cuts", [])
                   if c["start_ms"] < end and c["end_ms"] > start
                   and c["start_ms"] < c["end_ms"]), key=lambda c: (c["start_ms"], c["end_ms"]))
    cited = set(treatment_refs)
    for cut in decision.get("cuts", []):
        cited.update(cut.get("evidence_ids", []))
        cited.update(cut.get("requires_retained_ids", []))
    dense = [f for f in facts if (f.get("evidence_kind") == "dense_visual" or f.get("frame_times_ms"))
             and f["start_ms"] < end and f["end_ms"] > start
             and f["start_ms"] < f["end_ms"]]
    dense.sort(key=lambda f: (f["id"] not in cited, not bool(f.get("frame_times_ms")),
               not any(f["start_ms"] < c["end_ms"] and f["end_ms"] > c["start_ms"] for c in cuts),
               f["end_ms"] - f["start_ms"], f["start_ms"], f["id"]))
    identity = {"version": 2, "source_id": source_id, "source_identity": source_identity,
                "duration_ms": duration_ms, "core": list(core), "decision": decision,
                "evidence_revision": content_digest(facts),
                "treatment_refs": sorted(cited), "max_frames": max_frames}
    selected: dict[int, dict[str, Any]] = {}
    anchors: list[dict[str, Any]] = []
    endpoints: list[dict[str, Any]] = []
    context: list[dict[str, Any]] = []

    def candidate(time: int, kind: str, refs: list[str], basis: str) -> dict[str, Any]:
        return {"timestamp_ms": max(start, min(end - 1, time)), "kind": kind,
                "evidence_ids": refs, "basis": basis}

    if start < end:
        for fact in dense:
            middle = (max(start, fact["start_ms"]) + min(end, fact["end_ms"]) - 1) // 2
            observed = [t for t in fact.get("frame_times_ms", [])
                        if start <= t < end and fact["start_ms"] <= t < fact["end_ms"]]
            time = min(observed, key=lambda t: (abs(t - middle), t)) if observed else middle
            anchors.append(candidate(time, "anchor", [fact["id"]],
                                     "supporting_observation" if observed else "interval_midpoint"))
        ranked_cuts = sorted(cuts, key=lambda c: (
            not any(f["id"] in cited and f["start_ms"] < c["end_ms"]
                    and f["end_ms"] > c["start_ms"] for f in dense),
            -(c["end_ms"] - c["start_ms"]), c["start_ms"]))
        for cut in ranked_cuts:
            for time in (cut["start_ms"], cut["end_ms"] - 1):
                endpoints.append(candidate(time, "cut_endpoint", [], "proposed_omission"))
        for fact in dense:
            for time in (fact["start_ms"] - 1000, fact["end_ms"] + 1000):
                context.append(candidate(time, "anchor_context", [fact["id"]], "neighboring_state"))

    def take(items: list[dict[str, Any]], quota: int) -> None:
        added = 0
        for item in items:
            time = item["timestamp_ms"]
            if time in selected:
                if item not in selected[time]["requests"]:
                    selected[time]["requests"].append(item)
                continue
            if added >= quota or len(selected) >= max_frames:
                continue
            selected[time] = {"frame_id": f"{source_id}-frame-{content_digest([source_identity, time])[:16]}",
                              "timestamp_ms": time, "requests": [item]}
            added += 1

    take(anchors, 8)
    take(endpoints, 4)
    take(context, 4)
    take(anchors + endpoints + context, max_frames)
    if not selected and start < end:
        take([candidate(t, "overview", [], "unlocalized_question")
              for t in (start, (start + end - 1) // 2, end - 1)], max_frames)
    served = {ref for row in selected.values() for request in row["requests"]
              if request["kind"] == "anchor" for ref in request["evidence_ids"]}
    missing = sorted(cited - {f["id"] for f in dense})
    return {"packet_id": content_digest(identity), "identity": identity,
            "source_id": source_id, "question": decision.get("question", ""),
            "frames": [selected[t] for t in sorted(selected)],
            "unserved_anchor_ids": [f["id"] for f in dense if f["id"] not in served],
            "unavailable_visual_reference_ids": missing,
            "unserved_endpoint_times_ms": sorted({r["timestamp_ms"] for r in endpoints} - selected.keys()),
            "limitations": ["Sampled frames do not establish continuous interval coverage.",
                            "Interval midpoints are search locations, not verified supporting observations.",
                            "Retrieval is confined to the core and source; unavailable references are not substituted."]}
