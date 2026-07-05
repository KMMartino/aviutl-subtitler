# Packaging

This project can build a Windows Electron installer that bundles the UI and Python backend source, while leaving large runtime assets managed per user.

## Build Commands

```powershell
cd frontend
npm install
npm run dist:dir
npm run dist
```

Outputs are written under:

```text
release/
```

`dist:dir` creates an unpacked app at:

```text
release/win-unpacked/AviUtl Subtitler.exe
```

`dist` creates both NSIS and portable artifacts.

## Bundled Files

The installer includes:

```text
frontend/dist
frontend/dist-electron
aviutl_subtitle.py
subtitler/
configs/
requirements.txt
.env.example
README.md
WINDOWS_SETUP.md
```

The installer does not include model files, llama.cpp binaries, user `.env`, media files, or generated subtitle outputs.

## Installed User Data

In packaged mode, mutable app data is stored under Electron's user data directory, typically:

```text
%APPDATA%\AviUtl Subtitler
```

The app stores managed files in:

```text
configs/
models/
tools/llama/
tools/ffmpeg/
python/
.env
glossary.txt
settings.json
```

Development mode still uses the repo-local `.frontend-state` directory.

## Runtime Setup

The packaged app resolves runtime tools in this order:

Python:

```text
selected Python path
managed app venv under user data
python on PATH
missing
```

FFmpeg:

```text
managed FFmpeg under user data
ffmpeg/ffprobe on PATH
missing
```

The Settings screen exposes runtime status and actions for creating a managed Python venv, installing Python requirements, and downloading managed FFmpeg.

## Manual Smoke Test

1. Run `npm run dist:dir`.
2. Launch `release/win-unpacked/AviUtl Subtitler.exe`.
3. Open Settings.
4. Confirm Python and FFmpeg runtime statuses are visible.
5. If FFmpeg is missing, run the managed FFmpeg download.
6. If Python is missing, create the managed env and install requirements.
7. Configure hosted API keys in the app-managed `.env`.
8. Run a short hosted workflow and confirm an `.exo` file is created.
9. Restart the app and confirm settings persist.
10. Run `npm run dist` and install the NSIS build.

## Known Limitations

- The installer is unsigned.
- Python itself is not bundled; managed venv creation requires a system Python.
- Python dependency locking is not yet complete; managed requirements currently use `requirements.txt`.
- First-run model and llama-server downloads can be large and require network access.
