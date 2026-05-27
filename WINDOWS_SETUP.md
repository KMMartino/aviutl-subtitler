# Windows Setup

This project generates AviUtl `.exo` subtitles from local audio/video files on Windows. The supported transcription path is managed `llama-server.exe` with Gemma audio and an mmproj file.

## Required Tools

- Python 3.11+
- FFmpeg on `PATH`
- `llama-server.exe`, usually at `C:\tools\llama-vulkan\llama-server.exe`
- Gemma audio GGUF model
- Gemma audio projector/mmproj
- Optional cleanup GGUF model

Install Python dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For hosted API experiments, copy the example environment file and paste in only the keys you need:

```powershell
Copy-Item .env.example .env
```

`.env` is ignored by git and loaded automatically. Use `--env-file PATH` to point at a different file.

Hosted API runs estimate cost after VAD and before sending audio/text to a provider. The default guard stops runs estimated above `$5.00`; use `--allow-api-spend` only after reviewing the estimate.

For hosted drag-and-drop testing, create a shortcut to:

```text
run_subtitler_hosted_drop.bat
```

## Quick Test

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py test.m4a `
  --audio-track 0 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  -o diagnostics\test_server.exo
```

## Drag And Drop

Create a shortcut to:

```text
run_subtitler_drop.bat
```

Then drag a video file onto it. The batch file writes the `.exo` beside the source video and diagnostics under `subtitle_files`.

The batch file currently enables:

```text
--offline-model-cache
--profile
--llm-split-diagnostics
--cleanup-model ... when configured
--llm-split-planning cleanup-model when cleanup is configured
```

## Common Options

```text
--audio-track 0                   Use the first audio stream
--env-file PATH                   Load API keys/settings from a dotenv-style file
--transcriber-backend local-gemma|gemini|openai
--transcription-model MODEL       Hosted transcription model
--transcription-workers N         Concurrent hosted transcription requests
--cleanup-backend none|local-llama|gemini|openai
--cleanup-api-model MODEL         Hosted cleanup/review model
--estimate-cost-only              Estimate hosted API cost after VAD and exit
--tuning-profile auto|local|hosted
--cleanup-window-subtitles N      Lines per cleanup request
--cleanup-workers N               Concurrent hosted cleanup requests
--chain-split-workers N           Concurrent chain splitting workers
--skip-final-review               Skip final possible-mistranscription review
--llama-server PATH               Override transcription llama-server.exe
--server-port PORT                Override transcription server port, default 8081
--cleanup-llama-server PATH       Override cleanup llama-server.exe
--cleanup-server-port PORT        Override cleanup server port, default 8082
--cleanup-ctx-size N              Cleanup context size
--sidecar-dir PATH                Diagnostics/intermediate output directory
```

See `README.md` for the current full option list and the removed pre-release options.

## Notes

- `--language ja` is the default. Internally the CTC aligner receives `jpn`.
- Japanese alignment always uses char splitting and edge-only wildcard placement.
- Regrouping is always enabled.
- Cleanup mode is full when `--cleanup-model` is provided.
- The app inserts the initial empty EXO object and chain marker objects by default.
