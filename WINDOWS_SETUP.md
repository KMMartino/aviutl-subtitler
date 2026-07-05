# Windows Setup

This project generates AviUtl `.exo` subtitles from local audio/video files on Windows.

The supported app surface is four workflows:

```text
local
hosted
local-long-stream
hosted-long-stream
```

Use the batch files for normal runs:

```text
run_subtitler_drop.bat
run_subtitler_hosted_drop.bat
run_subtitler_long_stream_drop.bat
run_subtitler_long_stream_hosted_drop.bat
```

## Requirements

- Python 3.11+
- FFmpeg on `PATH`
- For local workflows: `llama-server.exe`
- For local workflows: Gemma audio GGUF model
- For local workflows: Gemma audio projector file
- For local cleanup: cleanup GGUF model
- For hosted workflows: API keys in `.env`

## Python Environment

```powershell
python -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install --upgrade pip
.\.venv-win\Scripts\python.exe -m pip install -r requirements.txt
```

## FFmpeg

Install FFmpeg and make sure `ffmpeg` and `ffprobe` are available on `PATH`.

```powershell
ffmpeg -version
ffprobe -version
```

## llama.cpp Server

For most local workflow users, the Electron frontend can download and manage `llama-server.exe`:

```powershell
cd frontend
npm run start
```

Open **Settings**, choose a managed server backend, then use **Check latest**, **Download server**, and **Use managed server**.

Supported managed backends:

```text
Vulkan: recommended for AMD on Windows and broadly compatible on Windows GPUs
CUDA 12.4: recommended NVIDIA option for this app
```

Managed installs live under:

```text
.frontend-state\tools\llama
```

The frontend does not replace an existing valid `llama-server.exe` path automatically. It updates the workflow config only when you click **Use managed server**.

Manual Vulkan install is still available:

```powershell
.\install_vulkan_llama.ps1
```

The default configs expect:

```text
C:\tools\llama-vulkan\llama-server.exe
```

Update `configs/local.json` and `configs/local-long-stream.json` if your path differs and you are not using the frontend-managed path.

## Local Model Paths

Local workflow paths live in:

```text
configs/local.json
configs/local-long-stream.json
```

Edit these fields for your machine:

```text
backend.model
backend.mmproj
backend.llama_server
cleanup.model
cleanup.llama_server
```

## Hosted API Keys

Copy the example env file:

```powershell
Copy-Item .env.example .env
```

Then add the provider keys needed by hosted workflows. Key values stay in `.env`; hosted model names live in:

```text
configs/hosted.json
configs/hosted-long-stream.json
```

## Running

Drag a video file onto one of the four batch files.

Direct CLI examples:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" --workflow local
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" --workflow hosted
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" --workflow local-long-stream
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" --workflow hosted-long-stream
```

Outputs are written beside the input file by default. Diagnostics are written under `subtitle_files`.

## Audio Track

The configs default to audio track `1`, the second stream. For files with only one audio stream, either edit the config:

```text
audio.track
```

or use the public override:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py input.mkv --workflow local --audio-track 0
```

## Tests

```powershell
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -v
```

## Electron Frontend

The frontend can run as a development app or be packaged as a Windows Electron app.

```powershell
cd frontend
npm install
npm run start
```

It creates managed configs under `.frontend-state\configs` and calls `aviutl_subtitle.py` as a subprocess.

Drop media onto the input panel or use the file picker. **Analyze** uses `ffprobe` to list the available audio tracks before generation. Both `ffmpeg.exe` and `ffprobe.exe` must be on `PATH`.

Diagnostic sidecars are enabled by default and can be disabled in the input panel for EXO-only output. The Python field should normally resolve to `.venv-win\Scripts\python.exe`; change it only when the project environment is elsewhere.

### Managed local models

For local workflows, choose a models directory, select an 8 GB, 12 GB, or 16 GB Gemma profile, and use **Download model profile**. Profiles use progressively larger models or quantizations while retaining runtime-context headroom:

```text
8 GB:  Gemma 4 E2B Q5 + E2B projector; E2B Q6 cleanup
12 GB: Gemma 4 E4B Q6 + E4B projector; 12B Q5 cleanup
16 GB: Gemma 4 E4B Q6 + E4B projector; 12B Q6 cleanup
```

The frontend writes these managed model paths into its workflow configs. It can also download a managed `llama-server.exe`:

```text
Vulkan: AMD-friendly Windows default
CUDA 12.4: NVIDIA Windows option
```

Use **Use managed server** to copy the downloaded executable path into both transcription and cleanup server fields. Existing valid manual server paths are left alone unless you explicitly switch.

Experimental MTP variants are available for all three tiers. They reuse downloaded target models and add matching Q8 MTP assistant files. Use a current llama.cpp build with `draft-mtp` support.

## Packaging

```powershell
cd frontend
npm run dist:dir
npm run dist
```

Package outputs are written under `release\` at the project root. The installer bundles the Electron app and Python backend source, but not GGUF models, llama.cpp server binaries, user API keys, or generated media/subtitle files.

Installed app state is stored under the Windows app data directory, normally:

```text
%APPDATA%\AviUtl Subtitler
```

See `PACKAGING.md` for the packaged runtime layout and smoke test checklist.
