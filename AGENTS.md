# Agent Notes

## Checks

Run the checks matching the changed code without waiting for approval.

Only write tests that test behavior of the program, not tests that merely duplicate implementation details.

For backend changes:

```powershell
python -m ruff check aviutl_subtitle.py subtitler tests
python -m mypy
python -m unittest discover -s tests
```

For frontend changes:

```powershell
cd frontend
npm run quality
npm test
```

For EXO-only styling or layout changes, the focused check is sufficient:

```powershell
.\.venv-win\Scripts\python.exe -m unittest tests.test_exo_markers
```

## App Testing

Rebuilding creates ignored artifacts under `release/` and always requires user approval. Propose a rebuild after a significant product change or an accumulation of smaller changes.

Whenever a rebuild has been approved or explicitly requested, run this deterministic project-root command instead of invoking `npm run dist` or copying the executable manually:

```powershell
.\rebuild-and-install.ps1
```

It builds the distribution, verifies the versioned portable artifact, and copies it to `C:\tools\personal\Subtitler-latest\SubUtl.exe` for user testing. Do not push until the user has tested and approved the build.

For EXO styling or layout changes, also generate a short EXO under `testing-grounds/` for visual inspection before pushing.

## Editorial Pipeline Versions

Long-stream editorial checkpoints reuse paid intermediate artifacts. The boundary versions live in `EDITORIAL_STAGE_VERSIONS` in `subtitler/editorial_project.py`.

Whenever a change alters a boundary's behavior, input assumptions, output schema, or interpretation, increment that boundary's integer before testing:

1. `source_probe`
2. `transcription`
3. `visual_learning`
4. `semantic_spans`
5. `local_reconciliation`
6. `global_reconciliation`

Increment the earliest affected boundary. Resume automatically invalidates that boundary and every downstream boundary, so do not increment downstream versions merely because an upstream artifact changed. The `semantic_spans` boundary currently produces interpreted spans, candidate edits, narration briefs, connections, and cumulative context. `local_reconciliation` turns that per-source output into durable project material. `global_reconciliation` produces the project-wide duration and selection plans. Put suggestion changes at the earliest boundary whose stored output would actually differ; use only `global_reconciliation` when all per-source analysis and candidate suggestions remain valid. Never change editorial boundary behavior without reviewing and, when applicable, incrementing this version vector.

## EXO Invariants

Normal subtitle objects include the sample reference's two `アニメーション効果` filters. In subtitle-only EXOs, QA/mistranscription markers use timeline `layer=2` above normal subtitles on layer 1. Composite media EXOs reserve layers 1-2 for linked video/audio, use layer 3 for normal subtitles, layer 4 for QA markers, and layer 5 for chapters. Paired editorial media additionally reserves layer 3 for the visible facecam track, shifting subtitles and all marker layers upward by one.

## Releases

Normal pushes to `main` run CI only. Publish Windows artifacts by pushing a version tag, for example:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The workflow takes the asset version from the tag rather than `frontend/package.json` and publishes `SubUtlSetup<version>.exe` and portable `SubUtl<version>.exe`. Neither bundles models, llama.cpp, Python, FFmpeg, or secrets.

## GitHub Actions Monitoring

Whenever an agent pushes a commit, tag, release, or performs another operation that triggers GitHub Actions, it must monitor every resulting workflow run until it reaches a terminal state. Do not treat a successful push as completion while its workflows are queued or running.

Use the GitHub CLI to identify runs for the exact pushed commit or tag, watch them through completion, and inspect failed job logs when necessary. Report the final status to the user, including which workflows and jobs passed or failed. For publication runs, also verify that the expected release and downloadable artifacts were actually created; a green build job alone is not sufficient.

If a workflow fails because of a small, well-contained mistake, diagnose it from the logs, implement and verify the correction, push the fix when appropriate, and monitor the replacement run without waiting for additional approval. Examples include formatting, lockfile drift, a narrowly scoped test failure, or a straightforward build configuration error.

If the remedy requires substantial refactoring, architectural changes, meaningful scope expansion, destructive history or tag rewriting, or a new release-version decision, stop and ask the user before proceeding. Transient infrastructure failures may be retried when doing so does not change code, tags, release identity, or external state beyond rerunning the failed workflow.
