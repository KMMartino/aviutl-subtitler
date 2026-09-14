# Offline AviUtl Subtitle Generator

Local Windows pipeline for generating AviUtl `.exo` subtitles from VOD audio.

The app composes reusable processing operations into subtitle and editing-guide workflows. Local and hosted subtitles share the same composition; silence markers use local VAD without transcription or cloud analysis. Model, VAD, alignment, cleanup, subtitle, and EXO settings live in `configs/`.

## Workflows

```text
local                Local Gemma transcription + local cleanup
hosted               Hosted transcription + tested cloud cleanup
hosted-long-stream   Local VAD -> source-media EXO with blank silence markers
```

The desktop starts with the desired output—subtitles or silence markers—and then
the available processing location. Workflow definitions declare their capabilities;
the unused local long-stream configuration and launcher have been removed.

```text
run_subtitler_drop.bat
run_subtitler_hosted_drop.bat
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

The internal workflow migration is tracked in
[plans/workflow-architecture.md](plans/workflow-architecture.md). The existing
subtitle modes share explicit transcription and subtitle-planning requests. Long-stream processing detects speech locally, protects acoustic edges, and exports blank silence markers alongside the original media. Paired recordings and multiple sources are supported; exports over 12 hours split at source boundaries. Managed projects retain intermediate artifacts and copy user-facing EXO files beside the media.

The editorial analysis backend is shelved for future workflows. Its CLI requires `--analysis` on `start` or `run`; the desktop does not invoke it. EXO re-import is not part of the active silence-marker workflow.

### Transcription segment policy

All providers use the same normalized transcript, forced-alignment, and subtitle-planning pipeline. Provider-specific behavior is kept in adapters and segment policy rather than separate backends.

- Hosted transcription defaults to `gemini-3.8-flash` with low thinking and extended request timeouts, with OpenAI `gpt-transcribe` as the distinct fallback. GPT Transcribe receives larger continuous VAD groups, which can span several minutes in the short workflow when the cleanup grouping limit permits it.
- Hosted cleanup defaults to OpenAI `gpt-5.6-luna` with low reasoning. Gemini cleanup offers 3.6 Flash/minimal as the economy profile and 3.7 Flash/low for stronger duplicate-fragment repair; 3.8 remains transcription-only in the approved catalog.
- Gemini transcription remains on fine VAD chunks capped at 30 seconds because longer groups failed the quality comparison.
- Local Gemma is hard-capped at 30-second VAD chunks. The 60-second packing trial was retired after it produced worse long-context substitutions; configuring a larger VAD maximum does not bypass the local cap.
- Padded VAD chunks can overlap slightly. Each chunk is aligned independently, then exact or fuzzy shared text is reconciled before an aligned-time seam assigns any remaining disagreement to one side.
- Alignment workers start while transcription is running and are capped at `min(configured workers, transcription segments)`.

Previous-transcript context is not added to every local request. Local transcription first tries the audio alone and uses preceding text only after ordinary and tighter-VAD recovery fail. Hosted transcription also starts context-free, then uses preceding text for a quality-failure retry when it is available.

### Subtitle split and cleanup policy

Subtitle planning ranks sentence and Japanese clause endings first, then connective/phrase boundaries, aligned acoustic pauses, LLM suggestions, duration limits, and the hard character fallback.

- Deterministic planning first exhausts sentence, clause, phrase, pause, title-safe, character, and duration evidence. For an ambiguous hard frontier it builds a small lattice of legal boundary IDs at roughly 60–100% of the available budget.
- Candidate boundaries keep contiguous katakana and kanji runs, common grammatical phrases, and compact numeric/date/time expressions intact. Among the remaining choices, clause/particle endings and hiragana-to-content-script transitions outrank arbitrary hiragana seams.
- Local cleanup models select one boundary ID per request and repeat against a rolling, context-sized prefix. Hosted cleanup models may select one ID from each of several frontier zones in a single request.
- The model never reproduces transcript text during split planning. Responses are sanitized to known IDs and every selected span is validated against the original aligned tokens before use. If a hosted response returns several alternatives from one zone, the planner scores the complete timed paths and keeps one boundary per zone; invalid or missing IDs fall back to deterministic splitting.
- Hosted cleanup output budgets scale with the text window. Cleanup keeps the same subtitle count, and a content-fingerprint guard rejects cross-boundary deletion or substantial rewriting.
- The final planner reapplies both the configured character and duration limits after boundary touching and timing adjustments.

## Command Line

The public CLI is workflow/config based:

```powershell
.\.venv-win\Scripts\python.exe aviutl_subtitle.py "C:\path\to\input.mkv" --workflow local
```

Available options:

```text
input
--workflow local|hosted|hosted-long-stream
--output PATH
--config PATH
--env-file PATH
--profile
--audio-track N
--sidecar-dir PATH
--cut-silence-encoder hevc-amf-cqp21|hevc-nvenc-qp21|hevc-qsv-q21|libx265-crf21
--transcript-artifact PATH
```

Everything else is configured in JSON.

## Config Files

Default configs live here:

```text
configs/local.json
configs/hosted.json
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
<output>.subtitles.json
<output>.transcript.json
```

The timed-text JSON stores text and timestamps together on the original source
timeline, before optional silence cuts. It records its source, audio track, raw
versus cleaned text policy, and completeness. It is a subtitle-plan artifact;
the complete aligned transcript is stored separately in `.transcript.json`, with
tokens, speech regions, source identity, settings, and a revision ID.

Pass `--transcript-artifact` to explicitly reuse a complete transcript for another
subtitle run. This skips audio extraction, transcription, and alignment; downstream
cleanup and export still run with the current settings. Reuse requires the same
audio track, source modification time, and sampled source fingerprint. It does not
silently rerun a model when an artifact is incompatible. Set `cleanup.backend` to
`none` in a workflow config to plan and export subtitles without model cleanup.

The shelved editorial CLI accepts `--transcript-artifact` with `--analysis` on `start` and `run`;
repeat it for multiple source files. Artifacts must match the project's sources
and selected audio track. Replacing an already completed transcript requires
`--restart-from transcription`, which invalidates its downstream results.

With sidecars retained, restarting a run reuses completed transcription
and subtitle planning. Changing cleanup or subtitle layout reruns planning without ASR;
changing export/B-roll settings preserves both. Checkpoints retain tokens, markers,
prior API usage, and dedicated transcript snapshots. B-roll separately saves its
catalog, model requests, submitted review, and web discovery. Failed attempts retain
known costs; corrupt artifacts stop instead of silently launching paid work.
Unsent review-screen edits are not saved. Completed runs can reuse the same results;
select “Start from the beginning” or pass `--fresh-run` to force new processing.

Editorial stages use the same immutable operation-result store. Each result records
its parameters, upstream revisions, and integrity checks; transcript files are also
checked by content hash. Changing recorded inputs restarts the earliest affected
boundary. New revisions use separate workspaces so partial-window caches cannot
leak across settings changes. Explicit restart creates a new revision while keeping
prior operation artifacts.

Both source panels accept a finished recording URL using the configured managed
yt-dlp installation. Recordings download in full to `SubUtl Sources` beside the
managed media directory, with a `source.json` provenance artifact. Downloading is
independent of paid processing and can be cancelled. Progress is shown for each
media file (video and audio download separately). The B-roll library's existing
rights/description admission and 20-minute window policy remain separate.


Cleanup may also write:

```text
<output>.final_text.txt
<output>.possible_mistranscriptions.txt
<output>.possible_mistranscriptions.raw.txt
```

## Electron Frontend

The Electron frontend manages user configs, edits core paths, streams Python logs, and opens generated outputs. It can run in development mode or be packaged as a Windows app.

```powershell
cd frontend
npm install
npm run start
```

The frontend writes local state under `.frontend-state/` and runs the same Python workflow CLI used by the batch files. It supports drag-and-drop input, `ffprobe` audio-track analysis, optional diagnostic sidecars, and eight light/dark themes.

Both `ffmpeg` and `ffprobe` must be available on `PATH`.

The main layout is:

```text
Input      select media and audio track
Outputs    edit EXO path, sidecar toggle/path, and open generated files
Settings   local model, llama-server backend, Python runtime, hosted API settings
Run/Logs   start/cancel processing and stream subprocess output
Glossary   edit project glossary.txt
```

The **Outputs** panel owns output paths. The EXO folder button opens the EXO location. Sidecar files can be enabled or disabled there; if the sidecar directory has not been generated yet, the sidecar location button opens its parent directory.

The short local and hosted workflows also offer **Cut silence** under Additional Settings. Proposed removals shorter than 0.5 seconds are ignored so tiny edits do not disrupt the video's flow. Enable **Review cuts** to open a full-window visual/audio seam review with **Accept cut**, **Reject cut**, and **Mark and reject** decisions; otherwise every safe VAD-derived internal cut is accepted automatically. By default, accepted cuts are non-destructive: the EXO contains contiguous video and linked-audio objects referencing the original constant-frame-rate source, using AviUtl's default first audio track. Keep the referenced source at the same path, as with ordinary AviUtl media objects. **Re-encode cut video** optionally creates a collision-safe constant-frame-rate `.cut.mkv`, retains all audio tracks as AAC, and makes the EXO reference that MKV; only this optional mode requires a validated encoder and `--cut-silence-encoder` for CLI use. Variable-frame-rate sources are warned about but never switch modes automatically.

For local workflows, the frontend manages selectable **8 GB**, **12 GB**, and **16 GB GPU Profiles (Gemma)** under its configurable models directory. Each profile installs an appropriately quantized transcription model, matching audio projector, and cleanup model while reserving VRAM for runtime context.

Local workflow settings are split into three collapsible sections:

```text
Local model
Server backend
Python runtime
```

The frontend can install a managed `llama-server.exe` under `.frontend-state/tools/llama`. Use **Vulkan** for AMD and broad Windows compatibility, or **CUDA 12.4** for NVIDIA. Downloading a new managed server switches the workflow to it. The app keeps the current and previous managed server builds and prunes older ones, so **Revert server** can switch back to the previous retained build. Manual server paths are still supported, and `install_vulkan_llama.ps1` remains available.

Settings also installs the pinned forced-alignment model into the managed model directory. Offline Hugging Face mode is enabled only after that local snapshot passes size and SHA-256 checks. On Windows, automatic alignment uses DirectML when a compatible DirectX 12 GPU and runtime are available; its first run converts and validates a reusable mixed-FP16 ONNX model cache, retaining numerically sensitive operations in FP32, while unsupported systems retain CPU alignment. The owned transcription server exits before alignment starts. DirectML uses batch size 1 and can select two isolated model processes for workloads with at least two 120-second jobs when the GPU has at least 12 GiB dedicated VRAM, the live DXGI budget has at least 8 GiB available, and at least 6 GiB system RAM remains available. Managed FFmpeg, llama.cpp archives, and model files are verified against upstream size/digest metadata before they are renamed, extracted, or selected; install metadata is retained beside each artifact.

Each hardware tier also has an experimental **MTP** profile. MTP profiles reuse the standard models and add small matching assistant GGUFs for llama.cpp multi-token prediction. They require a recent llama.cpp build and may not improve every workload or GPU.

Packaged builds are created with:

```powershell
cd frontend
npm run dist:dir
npm run dist
```

Build artifacts are written under `release/` at the project root. See `PACKAGING.md` for installed runtime paths and the smoke test checklist.

## Releases

Windows releases are published by pushing a version tag:

```powershell
git tag v0.1.1
git push origin v0.1.1
```

The release workflow validates the tag and writes its version into the Electron package metadata before building. Thus `v0.1.1` produces application, installer, and asset metadata for version `0.1.1` even if `frontend/package.json` was not edited beforehand.

GitHub Release assets use short names with no spaces:

```text
SubUtlSetup0.1.1.exe  installer version
SubUtl0.1.1.exe       portable version
```

The installer and portable app do not bundle model files, llama.cpp server binaries, Python, FFmpeg, or user secrets. Those are managed from inside the app.

## For Agents

Do not use subagents unless user specified or when you are confident that breaking out the task to a smaller model will result in higher quality code or in lower usage.

Always strive for simpler code in less lines. Avoid redundant checks, excessive testing, and checking the results of an action you just performed if the action has propper error logging and no errors were reported.
