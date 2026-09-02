# Agent Contract

## Communication

Respond in the language used by the user's latest prompt. Follow an explicit request for a different response language when provided.

## Verification

Run the checks for every changed surface without waiting for approval. A verification pass is complete when all applicable commands pass, or when the handoff names each failing command and distinguishes regressions from pre-existing failures.

Backend changes:

```powershell
python -m ruff check aviutl_subtitle.py subtitler tests
python -m mypy
python -m unittest discover -s tests
```

Frontend changes:

```powershell
cd frontend
npm run quality
npm test
```

EXO-only styling or layout changes:

```powershell
.\.venv-win\Scripts\python.exe -m unittest tests.test_exo_markers
```

Write tests for observable program behavior. Use existing implementation and configuration as the source of truth for mechanics.

## Product Testing

Rebuilds create ignored artifacts under `release/` and require explicit user approval. Propose a rebuild after a significant product change or an accumulation of smaller changes.

After approval, run this deterministic command from the project root:

```powershell
.\rebuild-and-install.ps1
```

The script builds the distribution, verifies the versioned portable artifact, and installs `C:\tools\personal\Subtitler-latest\SubUtl.exe`. Push only after the user has tested and approved that build.

For EXO styling or layout changes, also generate a short EXO under `testing-grounds/` for visual inspection before pushing.

## Editorial Improvement Loops

Run paid test-analyze-tune loops only when the user explicitly authorizes them. Treat each loop as one hypothesis tested against preserved evidence:

1. Preserve the canonical run. Copy its checkpoint, generated outputs, frame directory, and any mutable game-knowledge store into a dated iteration directory with `baseline`, `loop-1`, `loop-2`, and later subdirectories. Point every experimental run at its own copies so the original and earlier loops remain reproducible.
2. State the outcome defect and the earliest responsible editorial boundary. Prefer changing the prompt, evidence, schema, or planning contract that produces the decision; use downstream reconciliation only for irreducible integrity constraints. Update `EDITORIAL_STAGE_VERSIONS` according to **Editorial Checkpoints** before testing.
3. Run the applicable verification commands, then—after rebuild approval—run `rebuild-and-install.ps1`. Invoke the packaged editorial CLI from its packaged backend working directory using the exact command logged by the app as the source of truth. Substitute only the cloned checkpoint, workspace, and mutable knowledge paths. Use `--restart-from compatible` and confirm the log resumes at the intended boundary.
4. Let the run reach a terminal state. Record incremental and cumulative hosted cost, elapsed stages, action-type counts, manual-review count, narration count and placement, payoff/thread count, estimated duration, and audit replans. Inspect representative opening, middle, ending, and defect-specific sections; aggregate counts alone do not establish editorial quality.
5. Verify artifact integrity before tuning again: complete non-overlapping broad and action coverage, edit-safe speech boundaries, parent/child and supporting-edit referential integrity, narration/style-contract agreement, required assets and HTML images, EXO encoding, and contractual paired-media layers. Classify each difference from the preceding loop as improvement, regression, or lateral change.
6. Make the smallest upstream change that addresses the observed cause, preserve the completed loop, and repeat from step 2. Keep API concurrency within the user-authorized limit and reuse every checkpoint boundary whose stored contract still matches.

After the final loop, run the full applicable verification suite and one final rebuild. Open the generated HTML through a local HTTP server and check rendering, missing images, and horizontal overflow; inspect the EXO structure programmatically. Stop temporary servers, report the comparison across every loop, distinguish cumulative experimental cost from a clean-run estimate, and link the final artifacts. The session is complete only when the last run, artifact checks, tests, and rebuild all pass or every remaining failure is explicitly reported.

## Editorial Checkpoints

Editorial boundary changes: before testing, review `EDITORIAL_STAGE_VERSIONS` in `subtitler/editorial_project.py` whenever a change can alter a stored boundary's behavior, input assumptions, output schema, or interpretation. Long-stream resume reuses paid artifacts, so versioning is part of the change rather than release bookkeeping.

The boundaries, in invalidation order, are:

| Boundary | Stored responsibility |
|---|---|
| `source_probe` | Source inspection and normalized source facts |
| `transcription` | Transcript and aligned spoken evidence |
| `visual_learning` | Dense scene-state timeline, frame-change evidence, transition bursts, and reusable game knowledge |
| `semantic_spans` | Audio intent, edit-safe utterances, event graph, candidate edits, narration briefs, connections, safe boundaries, and cumulative context |
| `local_reconciliation` | Durable per-source project material |
| `global_reconciliation` | Project-wide story synthesis and backward payoff threads |
| `action_planning` | Authoritative source-timed child actions and aligned selected speech |
| `editorial_assets` | Resolved and verified reference-dependent frames |

Apply version changes in this order:

1. Identify the earliest stored artifact whose output can differ.
2. For a substantive change, increment that boundary before testing. Resume invalidates it and every downstream boundary automatically, so increment only the earliest affected boundary.
3. For a very minor tweak that may not justify discarding reusable paid artifacts, explain the expected checkpoint impact and ask the user whether to invalidate before changing the vector.

Use `global_reconciliation` alone when per-source analysis and candidates remain valid; use `action_planning` alone when synthesis remains valid but executable suggestions change; use `editorial_assets` alone when the action plan remains valid but selected reference material changes. This review is complete when every changed editorial behavior maps to one earliest boundary and the version vector reflects that decision.

## EXO Invariants

Normal subtitle objects include the sample reference's two `アニメーション効果` filters.

Layer assignments are contractual:

| Output | Media | Subtitles | QA | Chapters |
|---|---:|---:|---:|---:|
| Subtitle-only | — | 1 | 2 | — |
| Composite media | Video/audio 1–2 | 3 | 4 | 5 |
| Paired editorial media | Gameplay 1–2; facecam 3–4 | Above media | Above subtitles | Above markers |

## Releases and GitHub Actions

Normal pushes to `main` run CI only. Publish Windows artifacts with a version tag, for example:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The workflow derives the asset version from the tag and publishes `SubUtlSetup<version>.exe` and portable `SubUtl<version>.exe`. Release artifacts exclude models, llama.cpp, Python, FFmpeg, and secrets.

Any push, tag, release, or other action that triggers GitHub Actions remains in progress until every resulting workflow reaches a terminal state:

1. Use GitHub CLI to identify runs for the exact commit or tag.
2. Watch every run through completion.
3. Inspect failed job logs. Fix and verify a small, contained mistake—such as formatting, lockfile drift, a narrow test failure, or straightforward build configuration—and monitor the replacement run.
4. For publication, verify the release and expected downloadable artifacts in addition to the workflow result.
5. Report the final workflow and job status.

Retry transient infrastructure failures when the retry preserves code, history, tag identity, and release identity. Ask the user before substantial refactoring, architectural scope expansion, destructive history or tag rewriting, or choosing a new release version.
