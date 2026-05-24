# Windows Setup

This project generates AviUtl `.exo` subtitles from local audio/video files. Drag-and-drop uses managed `llama.cpp` server audio support through `llama-server.exe`. Native `llama-mtmd-cli.exe` remains the stable fallback, and the high-level `llama-cpp-python` chat handler remains research/debug only.

## Expected Layout

The drag-and-drop launcher assumes these paths:

```text
C:\coding\codex_projects\subtitler\
  aviutl_subtitle.py
  run_subtitler_drop.bat
  .venv-win\

C:\coding\0_models\gemma\gemma4-e4b-q6\
  google-gemma-4-E4B-it-Q6_K.gguf
  proj-for-q6.gguf

C:\tools\llama-vulkan\
  llama-server.exe
  llama-mtmd-cli.exe
```

## System Tools

Install Python 3.10 or newer from:

```text
https://www.python.org/downloads/windows/
```

Verify:

```powershell
py --version
python --version
pip --version
```

Install FFmpeg:

```powershell
winget install Gyan.FFmpeg
```

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

Install the latest AMD driver for Vulkan support:

```text
https://www.amd.com/en/support
```

Optional Vulkan verification:

```powershell
winget install KhronosGroup.VulkanSDK
vulkaninfo
```

## Python Environment

Create a Windows Python venv:

```powershell
py -3.11 -m venv .venv-win
.\.venv-win\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## llama.cpp

Use a recent Vulkan-enabled `llama.cpp` build containing:

```text
llama-server.exe
llama-mtmd-cli.exe
```

Install the latest release ZIP with:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_vulkan_llama.ps1
```

The script downloads:

```text
https://github.com/ggml-org/llama.cpp/releases/latest
```

and extracts `llama-<tag>-bin-win-vulkan-x64.zip` here:

```text
C:\tools\llama-vulkan\
```

The server backend automatically checks:

```text
C:\tools\llama-vulkan\llama-server.exe
```

The native fallback automatically checks:

```text
C:\tools\llama-vulkan\llama-mtmd-cli.exe
```

You can override the server path:

```powershell
python aviutl_subtitle.py input.mp4 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  --llama-server "C:\tools\llama-vulkan\llama-server.exe"
```

You can still force the native fallback:

```powershell
python aviutl_subtitle.py input.mp4 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend native `
  --llama-mtmd-cli "C:\tools\llama-vulkan\llama-mtmd-cli.exe"
```

## Model Files

Download the model:

```powershell
hf download BatiAI/gemma-4-E4B-it-GGUF `
  --include "google-gemma-4-E4B-it-Q6_K.gguf" `
  --local-dir "C:\coding\0_models\gemma\gemma4-e4b-q6"
```

Download the projector:

```powershell
hf download BatiAI/gemma-4-E4B-it-GGUF `
  --include "proj-for-q6.gguf" `
  --local-dir "C:\coding\0_models\gemma\projectors"
```

The projector is required for Gemma audio.

## Aligner And VAD Caches

`silero-vad`, PyTorch, and the CTC aligner use their normal cache locations by default. To centralize them, set these optional user environment variables:

```powershell
[Environment]::SetEnvironmentVariable("HF_HOME", "C:\coding\0_model_cache\huggingface", "User")
[Environment]::SetEnvironmentVariable("HF_HUB_CACHE", "C:\coding\0_model_cache\huggingface\hub", "User")
[Environment]::SetEnvironmentVariable("TRANSFORMERS_CACHE", "C:\coding\0_model_cache\huggingface\transformers", "User")
[Environment]::SetEnvironmentVariable("TORCH_HOME", "C:\coding\0_model_cache\torch", "User")
```

Open a new PowerShell window after setting persistent environment variables.

The Gemma audio model uses AMD/Vulkan through `llama.cpp`, but the forced aligner is PyTorch-based. On this machine PyTorch does not report CUDA, so `--alignment-device auto` runs alignment on CPU. The app parallelizes CPU alignment with `--align-workers`, defaulting to CPU count / 4.

## Verification

Verify the CLI:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py --help
```

Verify native Gemma audio fallback on the included small test file:

```powershell
powershell -ExecutionPolicy Bypass -File .\diagnose_native_llama_audio.ps1
```

Expected result includes a Japanese transcript similar to:

```text
どうも皆さんこんにちは...
```

Verify the full app against `test.m4a` with the server backend:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py test.m4a `
  --audio-track 0 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  -o diagnostics\test_server.exo
```

## Normal Use

Command-line run:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  -o "C:\path\to\input.exo"
```

Default audio track is `1`, the second audio stream in 0-based counting. Override it when needed:

```powershell
--audio-track 0
```

## Drag-And-Drop Use

The project includes:

```text
run_subtitler_drop.bat
```

To use it:

1. Right-click `run_subtitler_drop.bat`.
2. Choose **Create shortcut**.
3. Move the shortcut anywhere, such as the Desktop.
4. Drag a video file onto the shortcut.

The `.exo` file is written next to the video with the same basename.

The batch file uses:

```text
Model:       C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf
Projector:   C:\coding\0_models\gemma\projectors\proj-for-q6.gguf
Audio track: 1
Language:    ja
Backend:     server
```

The Python app starts `llama-server.exe`, waits for `/health`, sends each chunk to `/v1/chat/completions` using `input_audio` with base64 WAV data, and shuts the server down after the batch finishes.

The batch file also passes `--offline-model-cache`, which tells Hugging Face/Transformers to use the local cache for the CTC aligner instead of checking the Hub on every run. It also passes `--profile`, creating diagnostics under `subtitle_files` next to the input video.

For Japanese char alignment, the batch file also passes `--alignment-star-frequency edges`. This avoids inserting a CTC wildcard before every character, which can make split subtitle timings drift later until the next audio chunk resets alignment.

If a `glossary.txt` file exists next to the input video, it is used automatically. Format:

```text
PSSR | prefer over PSVR in graphics/upscaling context
PSSR Lite
AviUtl
```

Optional cleanup uses a second local text GGUF model. Edit this line in `run_subtitler_drop.bat`:

```bat
set "CLEANUP_MODEL=C:\coding\0_models\qwen\qwen3-14b-q6\Qwen3-14B-Q6_K.gguf"
```

When `CLEANUP_MODEL` is set, drag-and-drop adds `--cleanup-mode full` and `--llm-split-planning cleanup-model`. The cleanup server uses port `8082`.

## Diagnostics

Native llama.cpp diagnostic:

```powershell
powershell -ExecutionPolicy Bypass -File .\diagnose_native_llama_audio.ps1
```

Server backend full-app diagnostic:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py test.m4a `
  --audio-track 0 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  -o diagnostics\test_server.exo
```

Profile a run:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py test.m4a `
  --audio-track 0 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  --profile `
  -o diagnostics\test_profile.exo
```

Profiling also creates `<output>.regroup.csv`, which shows how aligned chunks were chained before resplitting and why each chain closed.
It also creates `<output>.subtitle_timing.csv`, which shows final subtitle timing, chain id, source split rule, token timing, prior gap, and timing adjustments.
It also creates `<output>.boundary_timing.csv`, which shows the token boundary text and lead-in pull applied between same-chain subtitle items.
It also creates `<output>.aligned_text.txt`, which shows raw aligned transcript text per VAD chunk before regrouping and subtitle splitting.

When `--llm-split-diagnostics` is enabled, the app writes `<output>.llm_split.csv`. If an LLM split plan is rejected, it also writes `<output>.llm_split.rejected.txt` with the attempted input text, raw model response, cleaned lines, and rejection reason.

Generated EXO files include empty timeline helper objects by default: one frame-1 anchor before the first subtitle, plus layer-2 markers spanning each merged regroup chain.

Subtitle resplitting uses multiple passes: sentence/connective splits, phrase punctuation for oversized blocks, LLM split planning, tighter phrase splitting, a second LLM pass, phrase splitting near the limit, then hard max-character splitting. A final left-merge pass joins adjacent subtitles when their combined text is still within the configured character limit. LLM split planning asks for a natural two-part split near the center; valid copied splits are accepted even when one side is short, and overly small adjacent fragments are repaired by the final merge pass.

The final possible-mistranscription report is produced only from the cleanup model's review. There is no hardcoded deterministic flag list.

Legacy Python binding diagnostic:

```powershell
.\.venv-win\Scripts\python.exe diagnose_gemma_audio.py test.m4a --keep-wav
```

The legacy Python binding path is expected to fail or produce template text on the current stack. It is kept for research only.

## Common Failures

### `llama-server was not found`

Put `llama-server.exe` at:

```text
C:\tools\llama-vulkan\llama-server.exe
```

or pass:

```powershell
--llama-server "C:\path\to\llama-server.exe"
```

### `llama-mtmd-cli was not found`

This applies only when using `--transcriber-backend native` or the native diagnostic. Put `llama-mtmd-cli.exe` at:

```text
C:\tools\llama-vulkan\llama-mtmd-cli.exe
```

or pass:

```powershell
--llama-mtmd-cli "C:\path\to\llama-mtmd-cli.exe"
```

### `Gemma projector file not found`

Download:

```text
proj-for-q6.gguf
```

to:

```text
C:\coding\0_models\gemma\
```

### `ModuleNotFoundError`

Use the project venv:

```powershell
.\.venv-win\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Alignment Falls Back To Proportional Timing

The program continues if the CTC model cannot load or if a non-length alignment failure occurs, but timing is less precise. Make sure the MMS aligner model is available in the Hugging Face cache and that `--language ja` matches the content.

If a chunk is too dense for CTC alignment, the program reruns VAD on that chunk with progressively tighter settings and retries on shorter subchunks. If this still fails after `--alignment-max-split-depth`, the run stops instead of silently accepting proportional timing for that chunk.

If `--offline-model-cache` is enabled before the aligner has been cached once, the aligner cannot download itself and will fall back to proportional timing. Run once without `--offline-model-cache`, or pre-download the aligner model into the configured Hugging Face cache.

### Cleanup server port is already in use

The cleanup model uses port `8082` by default. Pass another port:

```powershell
--cleanup-server-port 8083
```

### Cleanup model runs out of memory

Use a smaller quantization, omit cleanup, or run cleanup after closing other GPU-heavy apps. The app starts cleanup after the audio server is closed, but the text model still needs enough VRAM/system memory to load.

## New Quality Options

```text
--profile                         Write timing diagnostics
--profile-output PATH             Override profile CSV path
--audio-prep-workers N            CPU workers for audio payload prep
--transcription-max-split-depth N VAD re-split retries for suspect transcription output, default 2
--align-workers N                 Alignment workers; default CPU count / 4
--align-torch-threads N           PyTorch CPU threads per alignment worker
--align-emission-batch-size N     Batch size for CTC emission generation
--alignment-star-frequency edges|segment
                                  CTC wildcard placement, default edges
--alignment-max-split-depth N     VAD re-split retries for CTC-too-long chunks, default 4
--glossary PATH                   Use a specific glossary file
--no-glossary                     Disable glossary auto-discovery
--regroup-adjacent                Enable alignment-aware regrouping
--no-regroup-adjacent             Disable regrouping
--regroup-gap-sec SEC             Max gap for merging adjacent chunks, default 0.5
--regroup-max-window-sec SEC      Deprecated; kept for compatibility
--regroup-max-window-chars N      Deprecated; kept for compatibility
--regroup-ramp-start-sec SEC      Adaptive regrouping starts here
--regroup-ramp-step-sec SEC       Adaptive regrouping gap increment
--regroup-ramp-max-chain-sec SEC  Reject ramp chains longer than this
--regroup-ramp-max-chain-tokens N Reject ramp chains over this token count
--llm-split-planning off|cleanup-model
--llm-split-diagnostics           Write <output>.llm_split.csv and print split-plan status lines
--llm-split-max-input-chars N     Skip LLM split planning above this size, default 240
--llm-split-second-pass-max-input-chars N
                                  Skip second LLM split pass above this size, default 240
--chain-lead-in-sec SEC           Pull same-chain starts before first token, default 0.08
--chain-lead-in-growth-sec SEC    Add this much lead-in per chain part, default 0.0
--chain-lead-in-max-sec SEC       Cap same-chain lead-in, default 0.20
--cleanup-model PATH              Local GGUF text cleanup model
--cleanup-llama-server PATH       Cleanup llama-server.exe path
--cleanup-server-host HOST        Cleanup server host
--cleanup-server-port PORT        Cleanup server port
--cleanup-ctx-size N              Cleanup model context size
--cleanup-n-gpu-layers N          Cleanup model GPU layers
--cleanup-mode off|fillers|glossary|full
--cleanup-window-subtitles N      Subtitle lines per cleanup request
--no-initial-empty-exo-object     Disable the invisible frame-1 EXO alignment object
--sidecar-dir PATH                Directory for diagnostics/intermediate subtitle files
```

### Japanese Text Looks Wrong In AviUtl

The `.exo` file is written as Shift-JIS, and the subtitle text field is an AviUtl UTF-16LE hex buffer. Make sure the selected AviUtl font supports Japanese glyphs.

