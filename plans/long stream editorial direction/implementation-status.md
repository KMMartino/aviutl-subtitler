# Implementation status

## First vertical slice

Implemented and covered by automated tests:

- Full detected-speech transcription is the default for local and hosted long-stream workflows.
- High-activity-only transcription remains available as an optional additional setting.
- Hosted long-stream projects can enable suggestion-only editorial mapping.
- Multi-file selection, chronological reordering, total-duration display, required title/objective fields, optional must-keeps and de-emphasis notes, and a dual-ended final-duration control are available in the UI.
- A logical source may be one normal recording or a synchronized facecam/gameplay pair. Matching `xyz-role` and `xyz.role` names are classified with loose face/camera/game terms, then resolution; unresolved equal-resolution roles require user confirmation.
- Paired files must be within ten gameplay frames in duration. Otherwise they remain ordinary single-file sources. Confirmed pairs transcribe voice audio from the facecam file and sample vision from the gameplay file.
- Pair roles, the inference basis, durations, frame rate, paths, and independent fingerprints are stored in the checkpoint. Resume therefore does not rerun filename or resolution inference, and either moved member can be relinked independently.
- Sources are processed serially with a persistent cumulative context. Source-file boundaries receive no special editorial treatment.
- Source fingerprints are independent of names and timestamps. Resume verifies the original content and refuses mismatched replacements.
- Transcription, visual learning, semantic spans, local reconciliation, and global reconciliation have separate atomic checkpoints.
- Every durable boundary now carries a manually maintained integer version: source probe, transcription, visual learning, semantic spans, local reconciliation, and global suggestion generation. Resume compares the ordered vector and invalidates from the first mismatch through the end of the pipeline.
- Failed runs default to continuing at the exact incomplete source/stage. Successful compatible runs default to regenerating only global suggestions, while the user may deliberately restart any earlier boundary.
- Selecting the same fingerprinted media automatically offers reuse when its default sibling checkpoint exists. The manual checkpoint picker provides the same restart-boundary choice. Matching files moved to new paths are relinked before execution.
- `AGENTS.md` requires every future boundary behavior or artifact-contract change to review and increment the earliest affected version.
- Restart from checkpoint is exposed in the UI. Completed model stages are not repeated.
- The per-source semantic prompt records continuity, subtraction, selection, and context-dependency evidence separately. Silence is explicitly neutral.
- The project-wide pass creates continuity-led and selection-led proposals against the upper and lower requested duration bounds.
- Canonical JSON and a self-contained readable HTML report are written throughout the run, including on interruption.
- Actual hosted costs are accumulated in the artifact and report, with an initial project safety policy of $10 per source hour. Existing transcription estimates and approval guards remain active.

Production code and tests contain no reference to the FF16 example, its paths, its terminology, or creator-specific detectors.

## Deliberately incomplete

- The current first slice emits transcript EXOs per source but does not yet render the editorial suggestions as the planned layered marker EXO.
- HTML recommendations are readable but not yet an interactive timeline with filters or connection navigation.
- Matching a complete selected source set can relink moved files automatically. A partial per-source relinking dialog for projects whose remaining files are unavailable is not yet implemented; the backend relink command remains available.
- Global reconciliation uses the collected per-source recommendations and summaries. Retrieval over very large projects and refinement of cross-file thread matching will need real evaluation data.
- Cost accounting is durable, but vision and reasoning requests do not yet expose a complete preflight estimate alongside transcription before the run begins.
- No automatic edits, rearrangement, montage construction, chat processing, OCR, or diarization are implemented.

## Recommended next validation

Build the desktop app and use a small, non-reference project containing two or three short source files. Verify:

1. source selection, ordering, and duration-range interaction;
2. filename-, resolution-, and manually resolved facecam/gameplay pairing, including the ten-frame fallback;
3. full-transcript default and optional high-activity setting;
4. interruption during vision or semantic analysis followed by restart from checkpoint;
5. cross-file continuation in the HTML/JSON result;
6. whether continuity-led and selection-led recommendations are editorially distinct and useful;
7. actual cost and report readability.

Do not use the FF16 example as a required fixture or implementation dependency.
