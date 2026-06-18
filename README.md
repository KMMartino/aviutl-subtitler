# Offline AviUtl Subtitle Generator

Local Windows pipeline for generating AviUtl `.exo` subtitles from VOD audio.

The supported product surface is intentionally small: four workflows, each backed by a JSON config file. Fine-grained model, VAD, alignment, cleanup, subtitle, and EXO settings live in `configs/` instead of on the command line.

## Workflows

```text
local                Local Gemma transcription + local cleanup
hosted               Gemini transcription + OpenAI cleanup
local-long-stream    Full VAD markers, selected local transcription chunks
hosted-long-stream   Full VAD markers, selected hosted transcription chunks
```

The four drag-and-drop launchers map directly to those workflows:

```text
run_subtitler_drop.bat
run_subtitler_hosted_drop.bat
run_subtitler_long_stream_drop.bat
run_subtitler_long_stream_hosted_drop.bat
```

## Flow

```text
video/audio input
-> FFmpeg extracts mono 16 kHz WAV
-> transcription backend returns normalized timed transcript data
-> current backend: Silero VAD -> ASR -> CTC forced alignment
-> normalized transcript is adapted into subtitle-planner input
-> timing-aware subtitle chains are built
-> optional cleanup/boundary review/final candidate report
-> AviUtl .exo output
```

## Command Line

The public CLI is workflow/config based:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" --workflow local
```

Available options:

```text
input
--workflow local|hosted|local-long-stream|hosted-long-stream
--output PATH
--config PATH
--env-file PATH
--profile
--audio-track N
--sidecar-dir PATH
```

Everything else is configured in JSON.

## Config Files

Default configs live here:

```text
configs/local.json
configs/hosted.json
configs/local-long-stream.json
configs/hosted-long-stream.json
```

Local configs contain machine-specific paths for:

```text
Gemma GGUF model
Gemma projector
llama-server.exe
cleanup model
```

Hosted configs contain provider/model names only. API keys stay in `.env`.

## Environment

Copy the example environment file if using hosted APIs:

```powershell
Copy-Item .env.example .env
```

Fill in only the keys needed by the selected workflow. `.env` is ignored by git.

## Glossary

If `glossary.txt` exists next to the input video or in the project directory, it is loaded automatically.

## Diagnostics

Workflow configs currently enable diagnostics by default. Sidecars are written under `subtitle_files` beside the input unless `--sidecar-dir` is provided.

Typical sidecars:

```text
<output>.profile.csv
<output>.vad_selection.csv
<output>.regroup.csv
<output>.subtitle_timing.csv
<output>.boundary_timing.csv
<output>.aligned_text.txt
<output>.run.json
<output>.api_usage.csv
```

Cleanup may also write:

```text
<output>.final_text.txt
<output>.possible_mistranscriptions.txt
<output>.possible_mistranscriptions.raw.txt
```

## UI Direction

## Electron Frontend

The Electron frontend is a dev app that manages user configs, edits core paths, streams Python logs, and opens generated outputs.

```powershell
cd frontend
npm install
npm run start
```

The frontend writes local state under `.frontend-state/` and runs the same Python workflow CLI used by the batch files. It supports drag-and-drop input, `ffprobe` audio-track analysis, optional diagnostic sidecars, and eight light/dark themes.

Both `ffmpeg` and `ffprobe` must be available on `PATH`. Disable **Sidecar files** in the input panel when only the EXO output is needed.

For local workflows, the frontend manages selectable **8 GB**, **12 GB**, and **16 GB GPU Profiles (Gemma)** under its configurable models directory. Each profile installs an appropriately quantized transcription model, matching audio projector, and cleanup model while reserving VRAM for runtime context.

The frontend can also install a managed `llama-server.exe` under `.frontend-state/tools/llama`. Use **Vulkan** for AMD and broad Windows compatibility, or **CUDA 12.4** for NVIDIA. Existing working server paths are not overwritten; click **Use managed server** to switch a workflow config to the downloaded executable. The manual `install_vulkan_llama.ps1` helper remains available.

Each hardware tier also has an experimental **MTP** profile. MTP profiles reuse the standard models and add small matching assistant GGUFs for llama.cpp multi-token prediction. They require a recent llama.cpp build and may not improve every workload or GPU.
