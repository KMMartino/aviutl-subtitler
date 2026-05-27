# Offline AviUtl Subtitle Generator

Local Windows pipeline for generating AviUtl `.exo` subtitles from VOD audio.

The app is focused on Japanese audio. It transcribes with Gemma 4 audio through a managed `llama.cpp` server, aligns the transcript with CTC forced alignment, builds timing-aware subtitle chains, optionally runs a local cleanup model, and writes AviUtl `.exo`.

## Flow

```text
video/audio input
-> FFmpeg extracts mono 16 kHz WAV
-> Silero VAD splits speech chunks
-> llama-server transcribes each chunk with Gemma 4 + mmproj
-> ctc-forced-aligner aligns transcript timing
-> adjacent aligned chunks are regrouped into timing chains
-> deterministic token-boundary subtitle splitting
-> optional cleanup/boundary review/final candidate report
-> AviUtl .exo output
```

## Quick Start

Copy the example environment file if you plan to test hosted APIs later:

```powershell
Copy-Item .env.example .env
```

Fill in only the keys you need. `.env` is ignored by git and loaded automatically.

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  -o "C:\path\to\input.exo"
```

For files with only one audio stream:

```powershell
--audio-track 0
```

## Drag And Drop

Create a shortcut to:

```text
run_subtitler_drop.bat
```

Drag a video file onto it. The `.exo` file is written beside the source video, and sidecars are written under `subtitle_files`.

If `glossary.txt` exists next to the input video or in the project directory, it is loaded automatically.

## Cleanup Model

Cleanup is enabled when `--cleanup-model` is provided. It starts a second `llama-server.exe` on port `8082` by default and uses full cleanup mode.

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py input.mkv `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --cleanup-model "C:\coding\0_models\qwen\qwen3-14b-q6\Qwen3-14B-Q6_K.gguf" `
  --cleanup-ctx-size 32768
```

When cleanup is active, the model also reviews same-chain connective/punctuation boundaries and performs a final non-destructive candidate report for human review.

## Hosted API Benchmarks

The default path remains local Gemma. Hosted transcription and cleanup can be selected explicitly:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py input.mkv `
  --transcriber-backend gemini `
  --transcription-model gemini-2.5-flash `
  --cleanup-backend gemini `
  --cleanup-api-model gemini-2.5-flash `
  --llm-split-planning cleanup-model `
  --profile `
  -o input-gemini25.exo
```

Hosted runs estimate API cost after VAD and before spending. The default guard aborts a run above `$5.00` unless `--allow-api-spend` is provided. Actual provider token usage and computed cost are written to `<output>.api_usage.csv` and summarized in `<output>.run.json`.

For drag-and-drop hosted testing, use:

```text
run_subtitler_hosted_drop.bat
```

The hosted launcher uses Gemini 3.5 transcription, GPT-5.4 mini cleanup, hosted tuning, parallel hosted transcription, parallel chain splitting, parallel cleanup batching, and skips the final possible-mistranscription review by default.

## Diagnostics

`--profile` writes:

```text
<output>.profile.csv
<output>.regroup.csv
<output>.subtitle_timing.csv
<output>.boundary_timing.csv
<output>.aligned_text.txt
<output>.run.json
<output>.api_usage.csv
```

`<output>.run.json` records the backend/model settings, command-line arguments, and whether provider API keys were present. It does not store API key values.

`--llm-split-diagnostics` writes:

```text
<output>.llm_split.csv
<output>.llm_split.rejected.txt
```

Cleanup writes:

```text
<output>.final_text.txt
<output>.possible_mistranscriptions.txt
<output>.possible_mistranscriptions.raw.txt
```

## Main Options

```text
--audio-track N                   Audio stream index, default 1
--language LANG                   App language, default ja; Japanese maps to CTC jpn
--temp-dir PATH                   Temp root
--keep-temp                       Keep generated WAV chunks
--profile                         Write diagnostics
--env-file PATH                   Dotenv-style API key file, default .env
--estimate-cost-only              Estimate hosted API cost after VAD and exit
--max-estimated-api-cost-usd N     Hosted API cost guard, default 5.00
--allow-api-spend                 Allow runs over the estimate guard
--sidecar-dir PATH                Diagnostics/intermediate output directory

--transcriber-backend local-gemma|gemini|openai
--transcription-model MODEL       Hosted transcription model
--transcription-workers N         Concurrent hosted transcription requests
--model PATH                      Gemma GGUF model, required
--mmproj PATH                     Gemma audio projector, required
--llama-server PATH               llama-server.exe path
--server-port PORT                Transcription server port, default 8081
--n-gpu-layers N                  llama.cpp GPU layers, default all
--ctx-size N                      Transcription context size, default 8192
--audio-prep-workers N            Audio payload prep workers, default 2
--transcription-max-split-depth N VAD re-split retries for suspect transcripts, default 2

--alignment-model NAME            CTC alignment model
--alignment-device DEVICE         auto, cpu, or cuda
--alignment-max-split-depth N     VAD re-split retries for CTC-too-long chunks, default 4
--offline-model-cache             Use cached Hugging Face/Transformers files only
--align-workers N                 Alignment workers, default CPU count / 4
--align-torch-threads N           PyTorch CPU threads per aligner worker
--align-emission-batch-size N     CTC emission batch size, default 4

--max-chars N                     Max subtitle characters, default 40
--min-duration SEC                Minimum subtitle duration, default 0.40
--max-duration SEC                Maximum subtitle duration, default 6.0
--gap-threshold SEC               Touch nearby subtitles, default 0.25
--regroup-gap-sec SEC             Max gap for regrouping aligned chunks, default 0.5
--chain-lead-in-sec SEC           Same-chain lead-in before first token, default 0.08
--llm-split-planning off|cleanup-model
--llm-split-diagnostics
--chain-split-workers N           Concurrent chain splitting workers

--cleanup-model PATH              Local GGUF cleanup/review model
--cleanup-backend none|local-llama|gemini|openai
--cleanup-api-model MODEL         Hosted cleanup/review model
--tuning-profile auto|local|hosted
--cleanup-window-subtitles N      Lines per cleanup request
--cleanup-workers N               Concurrent hosted cleanup requests
--skip-final-review               Skip final QA review and layer-4 markers
--cleanup-llama-server PATH       Cleanup llama-server.exe path
--cleanup-server-port PORT        Cleanup server port, default 8082
--cleanup-ctx-size N              Cleanup context size, default 4096

--width N --height N --fps N
--font NAME --font-size N --y-position N
```

## Removed Pre-Release Options

The CLI was intentionally simplified. Removed options include:

```text
--llama-mtmd-cli
--verbose
--threads
--batch-size
--profile-output
--alignment-split-size
--alignment-star-frequency
--max-lines
--regroup-adjacent / --no-regroup-adjacent
--regroup-max-window-sec / --regroup-max-window-chars
--regroup-ramp-*
--llm-split-max-input-chars
--llm-split-second-pass-max-input-chars
--chain-lead-in-growth-sec
--chain-lead-in-max-sec
--cleanup-mode
--cleanup-server-host
--cleanup-n-gpu-layers
--no-initial-empty-exo-object
```

These were either unused, legacy debug paths, or tuning knobs whose current defaults are now part of the app behavior.
