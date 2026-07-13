# Project Review Backlog

This document records the repository-wide review performed on 2026-07-10. It is organized by implementation boundary so each boundary can be completed and manually tested before work moves elsewhere.

No item is considered complete until its automated checks and the boundary-specific manual test gate have passed.

## Rating legend

- **Priority:** Critical, High, Medium, or Low, based on user impact, correctness, security, and release risk.
- **Difficulty:**
  - **XS:** isolated change, usually under a few hours
  - **S:** localized change with focused tests
  - **M:** several related modules or nontrivial behavioral tests
  - **L:** cross-process, packaging, dependency, or migration work
  - **XL:** architectural work best divided into smaller follow-up items

## Recommended execution strategy

Favor small, high-value boundaries first. Within a boundary, finish the listed automated checks and manual test gate before starting the next boundary. The recommended initial order is:

1. Boundary A: configuration and cost safety
2. Boundary B: pipeline result and EXO correctness
3. Boundary C: CI, release metadata, and code-quality gates
4. Boundary D: hosted API reliability
5. Boundary E: managed runtime acquisition and integrity
6. Boundary F: llama-server and process lifecycle
7. Remaining frontend, performance, architecture, and accessibility boundaries

This order removes several high-risk issues with relatively confined changes before tackling packaging, process trees, and larger refactors.

---

## Boundary A — Python configuration and cost safety

Primary files: `subtitler/config.py`, `subtitler/backends/existing_pipeline.py`, `aviutl_subtitle.py`, configuration tests.

**Status:** Completed and independently verified on 2026-07-10. Focused tests: 37 passed. Full Python suite: 128 passed.

### 1. Prevent hosted cost-guard bypasses

- **Priority:** Critical
- **Difficulty:** S
- **Status:** Complete
- Require `allow_api_spend` and `estimate_cost_only` to be actual booleans.
- Reject `NaN`, infinity, and other non-finite numeric values.
- Reject nonstandard JSON constants at config parse time.
- Ensure the guard itself fails closed even if called with an invalid config.
- Add regression tests for string booleans, `NaN`, and infinity.

### 14. Complete workflow configuration validation

- **Priority:** High
- **Difficulty:** M
- **Status:** Complete
- Validate server ports, context sizes, worker counts, split depths, batch sizes, Torch threads, cleanup settings, diagnostics, cost flags, and all optional numeric values.
- Reject booleans where integer values are expected.
- Move toward one typed schema rather than scattered `int()` and `float()` conversions.

### 15. Normalize malformed-config errors

- **Priority:** Medium
- **Difficulty:** S
- **Status:** Complete
- Wrap JSON decoding, encoding, and file I/O failures in `SubtitlerError`.
- Include the file path and parse location in the user-facing message.

#### Automated gate

- Run the full Python unit suite.
- Add focused validation and CLI error-boundary tests.

#### Manual test gate

- Try valid local and hosted configs.
- Try malformed JSON, invalid types, non-finite costs, and a deliberately exceeded cost limit.
- Confirm the CLI and frontend show actionable errors and never authorize spend from malformed values.

---

## Boundary B — Pipeline result and EXO correctness

Primary files: `subtitler/backends/existing_pipeline.py`, `subtitler/transcription_backend.py`, `subtitler/exo.py`, related tests.

**Status:** Completed and independently verified on 2026-07-10. Focused tests: 15 passed. Full Python suite: 138 passed. No visual inspection was required because EXO layout and styling were unchanged; encoding was verified by a lossless Japanese round-trip and explicit rejection tests.

### 16. Report partial and failed transcription outcomes accurately

- **Priority:** Medium
- **Difficulty:** XS
- **Status:** Complete
- Return `partial` when any selected chunk fails.
- Return `failed` when no usable selected speech is produced.
- Preserve diagnostics and make the frontend/CLI surface the distinction.

### 28. Stop silently replacing unsupported EXO characters

- **Priority:** Medium
- **Difficulty:** S
- **Status:** Complete
- Validate Shift-JIS encodability for literal EXO fields.
- Report the offending font or setting instead of writing `?` silently.
- Document which fields are constrained by the EXO encoding.

#### Automated gate

- Run the Python suite, including focused backend-contract and EXO tests.

#### Manual test gate

- Generate one short EXO under `testing-grounds/`.
- Visually inspect normal output and diagnostic markers.
- Exercise one partial-transcription case and one unsupported EXO setting.

---

## Boundary C — CI, release metadata, dependencies, and quality gates

Primary files: `.github/workflows/`, `frontend/package.json`, `frontend/package-lock.json`, `requirements.txt`, new Python tooling configuration.

**Status:** Complete and user-verified on 2026-07-10. Installer/portable metadata, packaged startup, and the visible application UI are verified, and the portable executable is deployed to `C:\tools\personal\Subtitler-latest`. Automated verification also covers backend/frontend suites, static checks, release-version dry runs, a fresh hash-locked Windows environment, a successful five-minute local workflow smoke run, and a zero-finding npm audit.

### 3. Run backend checks in CI and release workflows

- **Priority:** High
- **Difficulty:** S
- **Status:** Complete
- Add Python setup, dependency installation, and the full unit suite to CI.
- Require backend and frontend checks before release packaging.

### 12. Derive the application version before building a tagged release

- **Priority:** High
- **Difficulty:** S
- **Status:** Complete
- Feed the validated tag version into Electron and NSIS metadata before `electron-builder` runs.
- Ensure filenames, application metadata, installer metadata, and upgrade behavior agree.

### 29. Add static-analysis and formatting baselines

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete
- Add Ruff and a gradual Pyright or Mypy baseline for Python.
- Add frontend linting, formatting, and selected stricter TypeScript checks.
- Add the checks to CI without requiring a repository-wide rewrite in one step.
- The intentionally scoped initial mypy and Prettier baselines, plus their expansion policy, are recorded in `QUALITY.md`.

### 6. Make Python dependency installation reproducible

- **Priority:** High
- **Difficulty:** L
- **Status:** Complete
- Pin supported dependency versions.
- Pin `ctc-forced-aligner` to a commit rather than a mutable branch.
- Introduce constraints or a lock file and, where practical, hashes.
- Define an update process for large ML dependencies.

### 5. Upgrade the Electron and Node build dependency chain

- **Priority:** High
- **Difficulty:** L
- **Status:** Complete
- Upgrade Electron from the current major and resolve the full `npm audit` findings.
- Recheck preload, process, packaging, and NSIS behavior after the major upgrade.
- Keep in mind that Electron is declared as a dev dependency but becomes the packaged runtime.

#### Automated gate

- Exercise the updated workflows locally where possible.
- Run Python tests, frontend typecheck, frontend tests, lint checks, and dependency audits.
- For release-version or Electron changes, obtain approval before running `npm run dist`.

#### Manual test gate

- Inspect application and installer version metadata from an approved test build.
- Launch the portable app and installer build, verify startup, and perform one short workflow smoke test.

---

## Boundary D — Hosted API clients and reliability

Primary files: `subtitler/external_transcribers.py`, `subtitler/external_refiners.py`, hosted-client tests.

**Status:** Complete and independently verified on 2026-07-10. Focused tests: 27 passed. Full Python suite: 150 passed. Live read-only Gemini model verification confirmed `x-goog-api-key` authentication; retry, fallback, `Retry-After`, and redaction behavior were verified deterministically without a paid transcription call.

### 7. Retry transient hosted transcription failures and then use fallback

- **Priority:** High
- **Difficulty:** M
- **Status:** Complete
- Retry 408, 429, and appropriate 5xx responses with bounded exponential backoff and jitter.
- Honor `Retry-After` where supplied.
- Invoke the configured fallback after retry exhaustion rather than leaving a blank region immediately.

### 19. Respect explicit hosted transcription worker settings

- **Priority:** Medium
- **Difficulty:** XS
- **Status:** Complete
- Use six workers only as the default when no value is supplied.
- Respect explicit lower values for rate-limit and resource control.

### 27. Move Gemini credentials out of query strings

- **Priority:** Medium
- **Difficulty:** S
- **Status:** Complete
- Use the provider-supported API-key header for transcription, model verification, and refinement.
- Ensure errors and diagnostics cannot echo credentials.

### 24a. Consolidate hosted HTTP policy

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete
- Share timeout, retry, error classification, redaction, and response-decoding behavior between hosted transcription and refinement.

#### Automated gate

- Add deterministic tests for 429, retryable 5xx, `Retry-After`, timeout, fallback, malformed responses, and credential redaction.
- Run the full Python suite.

#### Manual test gate

- Run a short hosted transcription with valid credentials.
- Simulate or safely induce a retryable failure and confirm retry/fallback logging and final output.
- Verify no API key appears in logs or error text.

---

## Boundary E — Managed runtime acquisition and artifact integrity

Primary files: `frontend/src/main/ffmpegManager.ts`, `localModels.ts`, `llamaServerManager.ts`, `pythonRuntime.ts`, runtime UI and tests.

**Status:** Complete on 2026-07-13. Automated integrity, cache, recovery, and workflow checks pass; the packaged setup UI and local workflow were user-verified. The user explicitly accepted the managed-download path without repeating a clean-state download/interruption test for this review cycle.

### 2. Provide a clean-install path for the required alignment model

- **Priority:** High
- **Difficulty:** L
- **Status:** Complete; automated checks pass and packaged setup behavior was accepted
- Add managed download/cache detection for the forced-alignment model.
- Enable offline mode only when the required files are confirmed present.
- Surface download size, progress, failure, and cache location in the setup UI.

### 4. Verify downloaded executables, archives, and models

- **Priority:** High
- **Difficulty:** L
- **Status:** Complete; automated checks pass and the remaining manual download exercise was explicitly waived for this review cycle
- Pin artifact versions where practical.
- Verify SHA-256 hashes or upstream signatures for FFmpeg and llama.cpp.
- Validate model size and preferably digests before completing a download.
- Invalidate corrupted cached artifacts automatically.
- Record installed artifact metadata for troubleshooting.

#### Automated gate

- Add tests for valid, truncated, corrupt, cached, and mismatched artifacts.
- Run frontend typecheck and tests.

#### Manual test gate

- Test setup from an empty managed state directory.
- Download the aligner, FFmpeg, one llama backend, and one model profile.
- Interrupt one download and verify the next attempt recovers cleanly.
- Run one short local workflow after setup.

---

## Boundary F — llama-server identity and process lifecycle

Primary files: `subtitler/transcriber.py`, `subtitler/text_refiner.py`, `frontend/src/main/runProcess.ts`, Electron lifecycle code, server tests.

**Status:** Complete on 2026-07-11; packaged cancellation and shutdown gates passed.

### 8. Verify local server and model identity before reuse

- **Priority:** High
- **Difficulty:** M
- **Status:** Complete
- Do not accept an arbitrary HTTP 200 health response as proof that the requested model is loaded.
- Verify server/model metadata, use per-run dynamic ports, or fail clearly on an occupied port.

### 9. Terminate the complete run process tree

- **Priority:** High
- **Difficulty:** L
- **Status:** Complete
- Make cancellation stop Python, FFmpeg, llama-server, and other descendants on Windows.
- Add graceful cancellation followed by forced termination.
- Clean active runs during application shutdown.

### 26. Close server log handles on every startup failure path

- **Priority:** Medium
- **Difficulty:** S
- **Status:** Complete
- Ensure early process exit and failed spawn paths close log files.
- Normalize startup exceptions consistently.

### 24b. Consolidate llama-server lifecycle code

- **Priority:** Medium
- **Difficulty:** L
- **Status:** Complete
- Share resolution, launch, health checking, identity verification, logging, shutdown, and failure cleanup between transcription and refinement.

#### Automated gate

- Add tests for port collisions, wrong-model reuse, early exit, timeout, cancellation, shutdown, and handle cleanup.
- Run Python and frontend checks.

#### Manual test gate

- Start and cancel both local workflows.
- Close the app during an active run.
- Confirm no Python, FFmpeg, or llama-server process remains and a subsequent run starts cleanly.

---

## Boundary G — Alignment and VAD performance

Primary files: `subtitler/alignment_pool.py`, `subtitler/backends/existing_pipeline.py`, `subtitler/vad.py`, profiling and performance tests.

**Status:** Complete on 2026-07-11; fixed-input output matched exactly and runtime improved from 122.2s to 115.5s.

### 11. Avoid loading multiple full aligner models on one GPU by default

- **Priority:** High
- **Difficulty:** L
- **Status:** Complete
- Default GPU alignment to one shared model instance with batching.
- Size CPU concurrency using memory as well as core count.
- Keep explicit advanced overrides available.

### 20. Reuse Silero VAD across split retries

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete
- Cache or inject a single VAD model per run.
- Reuse previously computed probabilities for subranges where possible.

### 20b. Make local LLM planning and cleanup performance observable

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete for the user-facing observability scope and fixed-input verified on 2026-07-13. Normal output reports coarse split-planning, boundary-review, and cleanup-group progress suitable for a future progress bar; detailed split and cleanup timing/statistics remain in the profiling sidecar, while lower-level model diagnostics stay in the llama-server log.
- Emit user-relevant start/completion progress for LLM split planning, boundary review, and cleanup groups without flooding the future progress display with internal diagnostics.
- Show the active model profile and cleanup model near run startup so an unintended profile switch is obvious.
- Preserve prompt/token throughput and GPU offload information in llama-server logs where available rather than duplicating it in normal progress output.
- Benchmark the 8 GB, 12 GB, and 16 GB profiles on the same fixed input before changing concurrency or batching.

### 20c. Make local cleanup output constrained, fail-closed, and bounded

- **Priority:** Critical
- **Difficulty:** L
- **Status:** Complete — local cleanup now uses an indexed one-result-per-input protocol with explicit `<DELETE>` output for verified filler-only subtitles. Missing, duplicate, reordered, malformed, explanatory, Markdown, prompt/glossary-echoing, expanded, contracted, or semantically changed output rejects the complete VAD cleanup group and retains its originals. Batch rejection never fans out into per-subtitle requests.
- Generated-token allowance grows with batch size but is bounded by both a fixed ceiling and the active context size. Rejection sidecars record the exact reason, input and response counts, raw response, finish reason, token usage, configured allowance, and whether generation appears token-limited.
- A controlled E2B A/B test ruled out the July 10 prompt rules, filler wording, prompt length/order, examples, and system-role wording as the cause of exposed reasoning. Local llama.cpp startup now passes explicit `--reasoning off` as well as `--reasoning-budget 0`; evidence is under `testing-grounds/cleanup-prompt-ab/`.
- Local cleanup no longer exposes the full glossary to the model. Exact case/spacing/separator variants are normalized deterministically after validation, while inferred substitutions are rejected. A semantic fingerprint permits only filler, punctuation, presentation, and exact glossary normalization changes; regressions cover the observed `State of Play` to `State of Decay` collision and `しません` to `します` polarity reversal.
- Filler-only subtitles use explicit deletion and have their timing absorbed by the previous surviving subtitle when possible, otherwise the next. Substantive deletion and ambiguous partial-speech deletion fail closed.
- Cleanup uses complete long VAD groups. Local duration policies are 8 GB `clamp(duration/8, 20, 180)`, 12 GB `clamp(duration/4, 40, 300)`, and 16 GB `clamp(duration/2, 60, 600)`; MTP variants match their base profiles and hosted workflows retain `clamp(duration/2, 60, 600)`.
- Fixed-input profile validation produced safe output with no layer-1 timing overlaps: 8 GB accepted 6/14 groups, 12 GB accepted 8/10, and 16 GB accepted 2/3. The remaining ambiguous corrections, malformed structure, and partial-speech deletion were correctly retained. Accepted diffs across the final artifacts contained no glossary contamination, polarity reversal, or semantic drift.
- Constrained JSON-schema output is deliberately out of scope: the verified indexed protocol is reliable, and replacing it would add complexity without a demonstrated benefit. Fine-grained diagnostics remain available in sidecars and llama-server logs rather than being promoted into normal progress output.

#### Automated gate

- Add concurrency, memory-policy, and VAD-reuse tests.
- Run the full Python suite and compare profiling output on a fixed fixture.

#### Manual test gate

- Run the same representative local input before and after the change.
- Compare runtime, peak GPU/CPU memory, alignment output, and subtitle timing.

---

## Boundary H — Frontend persistence and coherent application state

Primary files: `frontend/src/main/configStore.ts`, `frontend/src/renderer/App.tsx`, shared config types, persistence tests.

**Status:** Complete and user-verified on 2026-07-13. Packaged recovery, settings persistence, rapid workflow switching, and the shared alignment-model state were exercised successfully.

### 10. Validate and atomically write persistent frontend state

- **Priority:** High
- **Difficulty:** M
- **Status:** Complete and packaged-verified
- Add runtime schemas, versioning, and migrations.
- Write via temporary file plus atomic replacement, with recoverable backup behavior.
- Provide a startup error/reset interface rather than an indefinite loading screen.

### 17. Prevent stale async refresh results from overwriting current state

- **Priority:** High
- **Difficulty:** M
- **Status:** Complete and packaged-verified
- Associate environment, runtime, model, path, and llama refreshes with request IDs or abort signals.
- Commit a response only if its inputs still match the current selection.
- Run independent path checks concurrently.

### 23. Replace the effectively untyped workflow config contract

- **Priority:** Medium
- **Difficulty:** L
- **Status:** Complete
- Define the known workflow schema in shared code.
- Use explicitly typed extension fields for advanced settings.
- Validate both renderer-to-main IPC and files loaded from disk.

### 25a. Debounce and serialize settings persistence

- **Priority:** Medium
- **Difficulty:** S
- **Status:** Complete
- Avoid synchronous writes on every text-input event.
- Coalesce changes, serialize writes, and surface save errors.

### 25b. Share one alignment-model selection across every workflow

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete and packaged-verified
- Treat the verified alignment model as one application-level dependency for local, local long-stream, hosted, and hosted long-stream workflows.
- Remove per-workflow alignment selection state because the UI offers no meaningful model choice.
- Migrate existing workflow configs to the single shared selection without losing advanced alignment settings.
- Keep install, use, readiness, and deletion status coherent when switching workflows.

#### Automated gate

- Add tests for corrupt state, old schema migration, interrupted writes, save ordering, stale responses, and recovery UI.
- Run frontend typecheck and tests.

#### Manual test gate

- Corrupt and partially truncate copied state files, then launch the app.
- Rapidly switch workflows, model profiles, alignment readiness, and paths.
- Confirm current state wins, recovery is understandable, and saved settings survive restart.

---

## Boundary I — Electron main-process security, media analysis, and managed paths

Primary files: `frontend/src/main/main.ts`, `preload/preload.ts`, `mediaAnalyzer.ts`, `paths.ts`, `llamaServerManager.ts`, `index.html`.

**Status:** Complete and user-verified on 2026-07-13. Packaged path migration, repeated and rapid media selection, file/open actions, run locking, and constrained IPC interactions were exercised successfully.

### 13. Fix the double-nested managed llama path

- **Priority:** High
- **Difficulty:** M
- **Status:** Complete and packaged-verified
- Standardize path helpers around `RuntimePaths.userToolsRoot` or another single explicit contract.
- Migrate or detect the accidental `.frontend-state/.frontend-state/tools/llama` location.
- Update integration-style path tests and documentation.

### 18. Make media analysis cancellable and bounded

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete and packaged-verified
- Cancel ffprobe/FFmpeg rather than merely ignoring stale responses.
- Add timeouts and output-size bounds.
- Reject superseded analysis requests in the main process.

### 21. Validate and constrain privileged IPC

- **Priority:** Medium
- **Difficulty:** L
- **Status:** Complete and packaged-verified
- Validate every payload at runtime.
- Check sender origin and constrain workflows, enums, filesystem paths, downloads, process requests, and shell operations.
- Deny unexpected navigation and window creation.
- Add a restrictive Content Security Policy.

#### Automated gate

- Add main/preload contract tests, path-migration tests, invalid-payload tests, and media timeout/cancellation tests.
- Run frontend typecheck and tests.

#### Manual test gate

- Verify existing managed llama installations are discovered or migrated.
- Rapidly change media inputs and ensure old probes terminate.
- Exercise all file dialogs, open/show actions, downloads, and run startup after IPC validation is enabled.

---

## Boundary J — Frontend component architecture and long-session performance

Primary files: `frontend/src/renderer/App.tsx`, `components/SettingsPanel.tsx`, `LogViewer.tsx`, shared contracts and new domain hooks/controllers.

**Status:** Complete and user-verified on 2026-07-13. `SettingsPanel.tsx` is now a small composition wrapper, `App.tsx` has been reduced from 940 to 559 lines through focused runtime/local-model/llama/hosted controllers, and Python preparation, transcription, subtitle planning/refinement, and run artifacts are separated into typed modules. Two representative five-minute local runs preserved the expected 87-subtitle structure and timing, and the final packaged settings/workflow regression passed.

### 22. Split oversized frontend and Python orchestration units

- **Priority:** Medium
- **Difficulty:** XL
- **Status:** Complete and packaged-verified
- Split `App.tsx` into focused domain hooks/controllers and views.
- Split `SettingsPanel.tsx` into runtime, local, hosted, and output sections with focused view models/actions. All four sections are complete, including local-model and managed llama-server presentation.
- Move cross-process contracts into `src/shared`.
- Separately split Python CLI/orchestration and pure subtitle transformations when working in their respective boundaries. Run context, transcription, subtitle planning/refinement, and artifact writing now have typed module boundaries; the CLI remains the concise coordinator and EXO renderer caller.

### 25c. Bound and batch frontend log rendering

- **Priority:** Medium
- **Difficulty:** M
- **Status:** Complete
- Batch process-output events.
- Keep a capped ring buffer in the renderer.
- Optionally persist the full log to disk.
- Avoid re-rendering and scrolling the entire log for every chunk.

#### Automated gate

- Add focused hook/controller and component tests.
- Add a long-log performance test or benchmark.
- Run frontend typecheck and tests after each extraction step.

#### Manual test gate

- Exercise every settings section and workflow after refactoring.
- Run a long or synthetic high-volume log session and confirm memory and interaction remain stable.

---

## Boundary K — Accessibility and interaction behavior

Primary files: renderer components and `styles.css`.

**Status:** Complete and user-verified on 2026-07-13; packaged keyboard behavior, reduced-motion behavior, interaction states, and accessibility presentation were inspected successfully.

### 30. Improve keyboard, motion, and error accessibility

- **Priority:** Low
- **Difficulty:** M
- **Status:** Complete and user-verified
- Make resize separators keyboard-operable and expose ARIA values.
- Add keyboard glossary reordering.
- Honor `prefers-reduced-motion`.
- Use appropriate alert semantics and persistent inline errors.
- Add explicit accessible names to icon-only controls.
- Preserve user-controlled Settings section expansion instead of resetting it during status refreshes.

#### Automated gate

- Add component accessibility and keyboard-interaction tests.
- Run frontend typecheck and tests.

#### Manual test gate

- Navigate the full application using only the keyboard.
- Test with reduced-motion enabled and a screen reader or accessibility inspector.

---

## Boundary L — Test strategy and release confidence

This boundary supports all others and should be expanded incrementally rather than postponed until the end.

**Status:** Complete for the current review scope on 2026-07-13. Automated coverage includes hosted retry/fallback behavior, server lifecycle and identity, alignment integrity/resource policy, persistence recovery, stale media analysis, IPC validation, and the preload bridge contract. A deterministic generated-audio integration fixture exercises real FFmpeg probing/conversion and CLI orchestration through the VAD, local transcription, alignment, subtitle-planning, and EXO boundaries; only the model-backed stages use controlled in-process seams, avoiding API spend and model downloads. The isolated-profile portable smoke at `frontend/scripts/smoke-portable.ps1` passed against the deployed build, and the final packaged regression was user-verified. Python and frontend coverage reporting is installed through locked development dependencies, documented, and uploaded as non-blocking CI artifacts. The 2026-07-13 combined baseline is 60% overall Python coverage as reported by coverage.py and 35.97% frontend line coverage as reported by Vitest/V8. Percentage thresholds remain deliberately deferred until the architecture and legacy baseline stabilize.

### Test gaps to close alongside the relevant boundaries

- **Priority:** High
- **Difficulty:** XL overall; divide into S/M additions per boundary
- **Complete:** generated-audio integration fixture covering real FFmpeg conversion and CLI orchestration through EXO, with deterministic model seams.
- **Complete:** clean-install alignment-model behavior.
- **Complete:** 429/5xx retries and fallback.
- **Complete:** server identity, port collisions, cancellation, and shutdown.
- **Complete:** alignment concurrency and resource policy.
- **Complete:** IPC validation and preload contracts.
- **Complete:** frontend startup recovery, stale async responses, and core component interaction.
- **Complete:** packaged-application isolated-profile smoke test.
- **Complete:** non-blocking Python and frontend coverage reports, local commands, and CI artifacts.
- Later, consider focused thresholds for changed or high-risk boundaries; avoid an arbitrary repository-wide target.

Completed automated additions are covered by focused tests in `tests/`, `frontend/src/main/`, and `frontend/src/preload/`. The portable smoke script verifies that an isolated-profile build reaches a real application window and closes cleanly, without invoking a workflow or external API.

#### Manual test gate

- Maintain a short repeatable local workflow input and hosted workflow input.
- Record the expected EXO appearance, key logs, process cleanup, and setup behavior.
- Require the relevant smoke test after every completed boundary and before approval to push.

---

## Existing strengths to preserve

- Electron already uses context isolation and disables Node integration in the renderer.
- Child processes generally use argument arrays instead of shell interpolation.
- Managed deletion functions include path-containment guards.
- Downloads use `.part` files before rename.
- TypeScript strict mode is enabled.
- Subtitle heuristics and EXO marker behavior have useful focused tests.
- React does not use `dangerouslySetInnerHTML`.

## Review baseline

At the time of review:

- Python: 114 tests passed.
- Frontend: 43 tests passed across 10 files.
- Frontend TypeScript checks passed.
- `npm audit --omit=dev` reported no production dependency findings.
- Full `npm audit` reported four vulnerable dependency chains, including the packaged Electron runtime.
- No distributable rebuild was performed.
