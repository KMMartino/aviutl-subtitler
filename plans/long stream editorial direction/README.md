# Long-stream editorial direction

Status: the reduced human-information baseline is implemented and under product testing.

This directory preserves both the current hosted long-stream contract and the research/history that led to it. The active app produces factual editing evidence, sparse narration possibilities, selected subtitles, utterance guides, and deterministic voice-gap markers. Historical suggestion-heavy documents are not runtime specifications.

Earlier experiments compared continuity-first and selection-first editing. The active workflow deliberately leaves that judgment to the human editor.

## Documents

- [sample-analysis.md](sample-analysis.md) — reconstruction of the FF16 challenge-run example and its implications.
- [feature-plan.md](feature-plan.md) — proposed product behavior, processing architecture, artifacts, and staged delivery plan.
- [evaluation-plan.md](evaluation-plan.md) — how to evaluate useful recommendations without requiring a pre-existing machine-readable edit plan.
- [implementation-status.md](implementation-status.md) — current vertical-slice coverage, known gaps, and the next validation target.
- [human-information-baseline.md](human-information-baseline.md) — current reduced human-information output contract.
- [narration-practices.md](narration-practices.md) — compact agent-facing narration contract grounded in reference studies.
- `evidence/` — generated transcript output and future intermediate findings.

## Fixed decisions

- The initial run never makes semantic cut, preserve, montage, or creative-effect decisions.
- Hosted long-stream analysis always transcribes the full detected speech. High-activity-only transcription is retired.
- Long-stream analysis is hosted-only. Ordinary shorter subtitle workflows remain available locally.
- Multiple source videos are accepted in the UI, but analyzed serially, one file at a time. Persistent editorial state carries across file boundaries, and later linking treats cross-file and same-file relationships identically.
- Silence is neutral for factual event analysis. Separately, deterministic `[CUT]` guides mark voice-free gaps for human review without claiming that their visual content is unimportant.
- The user selects a desired final-duration range with a dual-ended slider. The UI does not need to display a compression ratio.
- A reviewed EXO is authoritative when applying cuts. Exact `[CUT]` objects on the reserved layer may be moved, resized, duplicated, or deleted by the user.
- Chat, chat OCR, speaker diarization, privacy UI, and automatic editing are out of scope for version 1.
- Required inputs are source video, sampled visual evidence, and a time-aligned transcript. Game title and run objective provide synthesis context.
- Canonical output is checkpointable JSON, accompanied by a readable HTML report and layered EXO markers.
