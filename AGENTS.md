# Agent Notes

## Checks

For frontend changes, run:

```powershell
cd frontend
npm run typecheck
npm test -- --run
```

For distributable-app changes, also run:

```powershell
cd frontend
npm run dist
```

`npm run dist` writes local ignored artifacts under `release/`.

## Baseline Testing and Deployment

For minor code changes, run only the standard code-level checks that match the files changed. Do this automatically when appropriate rather than waiting for user approval.

For frontend changes, the standard checks are:

```powershell
cd frontend
npm run typecheck
npm test -- --run
```

When a significant change has been made, or several smaller changes have accumulated into a significant product change, propose a distributable rebuild. Always check with the user before triggering a rebuild.

After each approved rebuild, copy the portable executable into:

```text
C:\tools\personal\Subtitler-latest
```

This path is on the user's `PATH` and is used for local usage testing.

After the user has completed actual usage testing and approves pushing, push the changes. Do not push before user usage testing approval.

## Releases

Normal commits to `main` should run CI only. Do not publish a distributable for every commit.

To publish a Windows release, make sure `frontend/package.json` has the intended version, then push a matching tag:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The release workflow builds on `windows-latest` and uploads two GitHub Release assets:

- `AviUtl Subtitler Setup <version>.exe`: installer version.
- `AviUtl Subtitler <version>.exe`: portable version, meant to run without installation.

The installer and portable builds intentionally do not bundle model files, llama.cpp server binaries, Python, FFmpeg, or user secrets.
