# Offline AviUtl Subtitle Generator

Local Windows pipeline for generating AviUtl `.exo` subtitle files from VOD audio.

The current drag-and-drop transcription backend is Gemma 4 audio through managed `llama.cpp` server (`llama-server.exe`). The native multimodal CLI (`llama-mtmd-cli.exe`) remains the stable fallback. The high-level `llama-cpp-python` audio path is retained only as a research/debug option because it did not correctly consume audio in this environment.

## What It Does

```text
video/audio input
-> FFmpeg extracts mono 16 kHz WAV
-> Silero VAD splits speech chunks
-> llama-server transcribes each chunk with Gemma 4 + mmproj
-> ctc-forced-aligner aligns raw transcript timing
-> adjacent aligned chunks are regrouped when safe
-> optional local text LLM cleans display text
-> AviUtl .exo writer outputs subtitle objects
```

The default target is Japanese audio. UI, CLI, and code are English.

## Key Defaults

```text
Model:       C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf
Projector:   C:\coding\0_models\gemma\projectors\proj-for-q6.gguf
llama.cpp:   C:\tools\llama-vulkan\llama-server.exe
Fallback:    C:\tools\llama-vulkan\llama-mtmd-cli.exe
Audio track: 1, the second audio stream
Output:      AviUtl .exo
```

## Quick Start

Read [WINDOWS_SETUP.md](WINDOWS_SETUP.md) first.

After setup, run:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
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

Move the shortcut anywhere, then drag a video file onto it. The `.exo` file is written beside the source video.

The launcher uses `--transcriber-backend server`. The Python app starts `llama-server.exe`, waits for `/health`, sends each chunk to `/v1/chat/completions`, then shuts the server down when the batch finishes.

The launcher also uses `--offline-model-cache` so the CTC aligner loads from the local Hugging Face/Transformers cache without checking the Hub on each run. It writes profile CSVs under `subtitle_files` beside the input video so alignment worker coverage, regrouping, and LLM split planning can be checked after a run.

For Japanese char alignment, the launcher uses `--alignment-star-frequency edges`. This keeps the CTC aligner's wildcard tokens at the transcript edges instead of inserting one before every character, which can otherwise make split subtitle timings drift later within each chunk.

If `glossary.txt` exists next to the input video, it is used automatically. To enable cleanup and LLM split planning, edit `CLEANUP_MODEL` in `run_subtitler_drop.bat` to point at a local text GGUF model.

## Glossary

Create `glossary.txt` next to the input video, or in the project directory:

```text
# preferred term | optional guidance
PSSR | prefer over PSVR in graphics/upscaling context
PSSR Lite
AviUtl
RDNA
```

The glossary is added to the Gemma audio prompt and to the optional cleanup prompt. It is a hint, not a forced replacement list.

## Cleanup Model

Cleanup is off by default. It uses a second managed `llama-server.exe` on port `8082` and starts after audio transcription has finished, so it does not compete with the Gemma audio server.

Recommended local text models:

```text
Japanese-heavy content: Swallow 8B Instruct GGUF, Q5_K_M or Q6_K
General multilingual:   Qwen3-14B GGUF, Q6_K
```

Manual example:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py input.mkv `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  --cleanup-model "C:\coding\0_models\qwen\qwen3-14b-q6\Qwen3-14B-Q6_K.gguf" `
  --cleanup-mode full `
  --cleanup-ctx-size 4096
```

Cleanup receives compact plain text plus glossary lines. It removes obvious fillers and fixes likely glossary terms, but subtitle timing remains based on the forced alignment from the raw transcript.

## Performance Profile

The server backend prepares the next audio payload on CPU while the current chunk is being processed, and it can align returned chunks in a background worker while sending the next request. It still sends only one audio request at a time because llama.cpp audio support is experimental.

Use:

```powershell
--profile
```

This writes `<output>.profile.csv` with per-chunk payload, transcription, and alignment timings. It also writes `<output>.regroup.csv` with alignment-chain diagnostics.
It also writes `<output>.subtitle_timing.csv`, which shows each final subtitle's chain id, token timing, source split rule, prior gap, and timing adjustments.
It also writes `<output>.aligned_text.txt`, which shows the raw aligned transcript per VAD chunk before regrouping and subtitle splitting.
These sidecar files are written to `subtitle_files` in the input video's directory by default.

Use:

```powershell
--llm-split-diagnostics
```

This writes `<output>.llm_split.csv` and prints a short line for each LLM split-planning attempt, showing whether the plan was accepted or why it was rejected. If any attempts are rejected, it also writes `<output>.llm_split.rejected.txt` with the input text, raw model response, cleaned response lines, and rejection reason. It does not print the full suggested subtitle text.

## Diagnostics

Test native Gemma audio:

```powershell
powershell -ExecutionPolicy Bypass -File .\diagnose_native_llama_audio.ps1
```

Test server Gemma audio through the complete app on the included small file:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py test.m4a `
  --audio-track 0 `
  --model "C:\coding\0_models\gemma\gemma4-e4b-q6\google-gemma-4-E4B-it-Q6_K.gguf" `
  --mmproj "C:\coding\0_models\gemma\projectors\proj-for-q6.gguf" `
  --transcriber-backend server `
  -o diagnostics\test_server.exo
```

The known-good test produced:

```text
どうもみなさんこんにちは快快です
```

## Important Files

```text
aviutl_subtitle.py                 Main CLI
run_subtitler_drop.bat             Drag-and-drop launcher
diagnose_native_llama_audio.ps1    Native llama.cpp audio diagnostic
diagnose_gemma_audio.py            Legacy llama-cpp-python diagnostic
subtitler/audio.py                 FFmpeg/WAV helpers
subtitler/vad.py                   Silero VAD segmentation
subtitler/transcriber.py           Server, native, and Python Gemma transcribers
subtitler/aligner.py               CTC forced alignment
subtitler/alignment_pool.py        Parallel alignment workers
subtitler/splitter.py              Subtitle shaping/splitting
subtitler/subtitle_planner.py      Alignment-aware regrouping and cleanup
subtitler/glossary.py              Plain-text glossary loading
subtitler/text_refiner.py          Plain-text cleanup LLM client
subtitler/profiling.py             Per-chunk timing CSV
subtitler/exo.py                   AviUtl .exo writer
aviutl_exo_format.md               EXO format notes
```

## CLI Options

```text
--profile                         Write <output>.profile.csv timing diagnostics
--profile-output PATH             Override profile CSV path
--audio-prep-workers N            CPU workers for payload preparation, default 2
--transcription-max-split-depth N VAD re-split retries for suspect transcription output, default 2
--align-workers N                 Parallel alignment workers, default CPU count / 4
--align-torch-threads N           PyTorch CPU threads per aligner worker
--align-emission-batch-size N     CTC emission batch size, default 4
--alignment-star-frequency MODE   CTC wildcard placement: edges or segment, default edges
--alignment-max-split-depth N     VAD re-split retries for CTC-too-long chunks, default 4
--glossary PATH                   Use a specific glossary.txt
--no-glossary                     Disable glossary auto-discovery
--regroup-adjacent                Merge adjacent aligned chunks when safe, default on
--no-regroup-adjacent             Disable adjacent regrouping
--regroup-gap-sec SEC             Max gap for regrouping, default 0.5
--regroup-max-window-sec SEC      Deprecated; kept for compatibility
--regroup-max-window-chars N      Deprecated; kept for compatibility
--regroup-ramp-start-sec SEC      Adaptive regrouping starts here, default 0.2
--regroup-ramp-step-sec SEC       Adaptive regrouping gap increment, default 0.1
--regroup-ramp-max-chain-sec SEC  Reject ramp chains longer than this, default 120
--regroup-ramp-max-chain-tokens N Reject ramp chains over this token count, default 900
--llm-split-planning MODE         off or cleanup-model; tries LLM split before mechanical max cuts
--llm-split-diagnostics           Write <output>.llm_split.csv and print accepted/rejected attempts
--llm-split-max-input-chars N     Skip LLM split planning above this size, default 240
--llm-split-second-pass-max-input-chars N
                                  Skip second LLM split pass above this size, default 240
--chain-lead-in-sec SEC           Pull same-chain subtitle starts before first token, default 0.08
--chain-lead-in-growth-sec SEC    Add this much lead-in per chain part, default 0.0
--chain-lead-in-max-sec SEC       Cap same-chain lead-in, default 0.20
--sidecar-dir PATH                Directory for diagnostics/intermediate subtitle files
--cleanup-model PATH              Local text GGUF model for cleanup
--cleanup-llama-server PATH       Cleanup llama-server.exe path
--cleanup-server-host HOST        Cleanup server host, default 127.0.0.1
--cleanup-server-port PORT        Cleanup server port, default 8082
--cleanup-ctx-size N              Cleanup model context, default 4096
--cleanup-n-gpu-layers N          Cleanup model GPU layers, defaults to --n-gpu-layers
--cleanup-mode MODE               off, fillers, glossary, or full
--cleanup-window-subtitles N      Cleanup lines per request, default 1
--no-initial-empty-exo-object     Do not insert an invisible frame-1 EXO alignment object
```

## Notes

- The server backend uses OpenAI-compatible chat completions with content type `input_audio` and base64 WAV data.
- Server transcription uses a Japanese strict prompt and explicit Gemma chat stop tokens. If a chunk returns an obviously incomplete, repeated, or assistant-contaminated transcript, that audio chunk is re-split with tighter VAD and retried before alignment.
- By default, EXO output inserts an empty text object from frame 1 to the first real subtitle. It also inserts empty layer-2 text objects spanning each merged regroup chain. These are timeline markers for checking video/subtitle alignment in AviUtl.
- Japanese char alignment uses edge-only CTC wildcard tokens by default. The older per-character wildcard mode can be selected with `--alignment-star-frequency segment`, but it can allow token anchors to drift later inside a chunk.
- If a transcript is too dense for the CTC aligner, the aligner reruns VAD on that audio chunk with tighter settings and retries on shorter subchunks instead of falling back to proportional timing.
- Same-chain subtitles use a small configurable lead-in before the next aligned token for readability. Progressive lead-in is disabled by default; boundary adjustments are logged in `<output>.boundary_timing.csv`.
- Regrouping uses an adaptive ramp: it first accepts tighter chains, then gradually relaxes toward `--regroup-gap-sec`. Candidate chains above the ramp length/token limits are split back into chunks for the next pass, which avoids extremely long drift-prone chains.
- Subtitle resplitting uses multiple passes: sentence/connective splits, phrase punctuation for oversized blocks, LLM split planning, tighter phrase splitting, a second LLM pass, phrase splitting near the limit, then hard max-character splitting. A final left-merge pass joins adjacent subtitles when their combined text is still within `--max-chars`.
- LLM split planning asks the model for a natural two-part split near the center. Valid copied splits are accepted even when one side is short; the final left-merge pass repairs overly small adjacent fragments.
- The final possible-mistranscription report is produced only from the cleanup model's review. There is no hardcoded deterministic flag list.
- The native backend starts `llama-mtmd-cli.exe` once per VAD chunk. This is reliable but slower than a persistent server.
- `Loading weights: 100%` from the aligner means cached weights are being loaded into memory. It is not necessarily a download.
- Alignment is PyTorch-based. On this AMD/Vulkan setup it runs on CPU; `--alignment-device auto` only uses CUDA when PyTorch reports an NVIDIA CUDA device.
- Multiple aligner workers each load their own aligner instance. If RAM or CPU contention is high, lower `--align-workers`.
- Generated `.exo` files are Shift-JIS text with AviUtl UTF-16LE hex subtitle buffers.

