# Feature Addition: Cut Silence

Cut Silence has been added for the short `local` and `hosted` workflows. It reuses raw intervals from the initial VAD pass to find internal silence, then either cuts every eligible interval automatically or pauses for user review. Long-stream workflows remain unsupported.

## What changed

- Added workflow configuration, CLI options, framed Electron/Python review events, candidate validation, timeline remapping, diagnostic manifests, and collision-safe MKV encoding.
- Accepted cuts now default to non-destructive EXO video/audio objects referencing the original CFR source. Rendering a separate CFR MKV is an optional per-workflow setting that is off by default.
- Added explicit AMD, NVIDIA, Intel, and CPU HEVC encoder presets with real FFmpeg compatibility probes for optional rendered output.
- Added a full-window review screen with Accept, Reject, and Mark-and-reject decisions, direct source playback, seam simulation, and bounded proxy fallback.
- Review previews initialize two seconds before each cut, explicitly support byte-range scrubbing, and keep Previous/dots/Next centered. Additional Settings uses compact chapter-marker-style checkboxes for Cut silence, Review cuts, and Re-encode cut video, with explanations in tooltips.
- Proposed removals shorter than 0.5 seconds are discarded before automatic cutting or review so negligible edits do not disrupt the video's flow.
- Accepted cuts remap subtitles, chapters, QA markers, and EXO duration. Composite media EXOs place `無音カット要確認` on layer 4; subtitle-only EXOs retain layer 2.
- Added settings migration/defaults, run blocking for invalid encoders or audio-only inputs, cleanup behavior, and backend/frontend test coverage.

## Main files touched

- Backend entry/config: `aviutl_subtitle.py`, `configs/*.json`, `subtitler/config.py`, `subtitler/run_context.py`
- VAD/transcription contract: `subtitler/vad.py`, `subtitler/transcription_backend.py`, `subtitler/transcription_stage.py`, `subtitler/backends/existing_pipeline.py`
- Cutting/remapping/output: `subtitler/silence_cut.py`, `subtitler/exo.py`, `subtitler/run_artifacts.py`
- Electron main process: `frontend/src/main/{main,python,runProcess,ipcSecurity,configStore}.ts`, plus `cutSilenceManager.ts` and `silencePreviewManager.ts`
- Renderer: `frontend/src/renderer/App.tsx`, `AdditionalSettingsPanel.tsx`, `SettingsPanel.tsx`, `SilenceReviewScreen.tsx`, `CutSilenceSettingsSection.tsx`, shared types/config helpers, and `styles.css`
- Tests: `tests/test_silence_cut.py`, workflow config tests, and the related frontend main-process/renderer tests
- Visual fixture: `testing-grounds/cut-silence-review-marker.exo`

The backend and frontend quality/test suites and the frontend production build passed. `npm run dist` has not been run.
