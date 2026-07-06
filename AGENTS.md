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
