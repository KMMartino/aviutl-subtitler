# Long-stream editorial direction

Status: product and analysis plan only. No implementation is authorized yet.

This directory records the proposed suggestion-only workflow for turning one or more long gameplay recordings or streams into an editorial map. The default target remains a lightly edited, mostly chronological video. Narration-heavy restructuring is an available recommendation style, not the default.

The planner recognizes two complementary editorial priors:

- **Continuity-first:** begin with the recording intact and justify each removal.
- **Selection-first:** begin with an empty timeline and justify each inclusion plus the context needed to understand it.

The desired runtime range controls how much each prior contributes, but the blend is decided per sequence and thread rather than imposed as one project-wide compression formula.

## Documents

- [sample-analysis.md](sample-analysis.md) — reconstruction of the FF16 challenge-run example and its implications.
- [feature-plan.md](feature-plan.md) — proposed product behavior, processing architecture, artifacts, and staged delivery plan.
- [evaluation-plan.md](evaluation-plan.md) — how to evaluate useful recommendations without requiring a pre-existing machine-readable edit plan.
- [implementation-status.md](implementation-status.md) — current vertical-slice coverage, known gaps, and the next validation target.
- `evidence/` — generated transcript output and future intermediate findings.

## Fixed decisions

- Version 1 produces suggestions and markers only; it never cuts, rearranges, or renders the sources.
- Full transcription is the default for local and hosted workflows. Existing high-activity-only transcription becomes an additional setting.
- The new editorial analysis is hosted-only. The smaller-segment local workflow remains available.
- Multiple source videos are accepted in the UI, but analyzed serially, one file at a time. Persistent editorial state carries across file boundaries, and later linking treats cross-file and same-file relationships identically.
- Silence is neutral evidence. It must never be used alone as a reason to cut gameplay.
- The user selects a desired final-duration range with a dual-ended slider. The UI does not need to display a compression ratio.
- The runtime target is not implemented as uniform trimming. The planner first uses high-confidence continuity-first removals, then applies selection-first construction only where greater condensation is needed and editorially supportable.
- Dynamic editorial handles are soft suggestions, not fixed trim points.
- Chat, chat OCR, speaker diarization, privacy UI, and automatic editing are out of scope for version 1.
- Required inputs are source video, sampled visual evidence, and a time-aligned transcript. Game title and run objective are required metadata; must-keep moments and subjects to de-emphasize are optional.
- Canonical output is checkpointable JSON, accompanied by a readable HTML report and layered EXO markers.
