# Agent Notes

## Checks

Run the checks matching the changed code without waiting for approval.

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

`npm run dist` creates ignored artifacts under `release/` and always requires user approval. Propose a rebuild after a significant product change or an accumulation of smaller changes.

After an approved rebuild, copy the portable artifact to `C:\tools\personal\Subtitler-latest\SubUtl.exe` for user testing. Do not push until the user has tested and approved the build.

For EXO styling or layout changes, also generate a short EXO under `testing-grounds/` for visual inspection before pushing.

## EXO Invariants

Normal subtitle objects include the sample reference's two `アニメーション効果` filters. In subtitle-only EXOs, QA/mistranscription markers use timeline `layer=2` above normal subtitles on layer 1. Composite media EXOs reserve layers 1-2 for linked video/audio, use layer 3 for normal subtitles, layer 4 for QA markers, and layer 5 for chapters.

## Releases

Normal pushes to `main` run CI only. Publish Windows artifacts by pushing a version tag, for example:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The workflow takes the asset version from the tag rather than `frontend/package.json` and publishes `SubUtlSetup<version>.exe` and portable `SubUtl<version>.exe`. Neither bundles models, llama.cpp, Python, FFmpeg, or secrets.
