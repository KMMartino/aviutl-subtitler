# Workflow architecture migration

Status: the next implementation pass for phases 1–3 is complete for the existing
supported flows, with final packaged verification complete. Phase 4 is a
workshop proposal; phase 5 remains ideas only. See
[creator-workspace-workshop.md](creator-workspace-workshop.md).

## Existing-flow migration checkpoint (2026-09-06)

- One parameterized speech-gap operation now serves subtitle silence cutting and
  editorial guide generation. Each composition keeps its existing handles, minimum
  gap, and edge policy. The 216 real-recording guides compare exactly with the
  previously verified output.
- One editorial stage lifecycle handles both source and project stages: progress,
  failure, completed-result publication, costs, and export recovery. Three model
  configuration functions are consolidated into one function parameterized by role.
  The raw-transcript composition accepts local, hosted, and long-stream settings.
- Editorial stages now use the same immutable `OperationStore` as subtitle and
  B-roll operations. Checkpoints reference result revisions. Input contracts include
  stage parameters, source facts, intent, and upstream revisions. Input changes
  invalidate the earliest affected boundary; explicit restarts create new revisions.
- A result is saved before checkpoint publication. Recovery can reuse that result
  even if publishing the checkpoint failed. Checkpoint loading verifies result
  integrity and referenced transcript-file hashes, including outside the runner.
  Source and project presentation fields are reconstructed from authoritative outputs.
- Each editorial operation generation uses a separate workspace. Interrupted windows
  remain reusable within that generation; changed inputs cannot consume another
  generation's partial cache. Known failure output and usage metadata remain in an
  immutable failure record after subsequent successful retries.
- Completed subtitle runs now reuse preparation. Cleanup settings belong to subtitle
  planning, not transcription matching. Changing cleanup/layout regenerates the
  plan; export-only changes reuse it. Fresh run explicitly bypasses reuse.
- Reviewed EXO narration references use the shared store per marker, keyed by its
  range, direction, factual context, candidates, locale and provider parameters.
  CLI usage includes retained review-attempt costs. Custom providers without explicit
  parameter identity opt out of caching.
- Editorial checkpoints, window progress, B-roll manifests and game-knowledge writes
  share the atomic JSON writer. Obsolete schema 1–3 checkpoint migrations and their
  compatibility-only tests are removed. Current schema 4 remains supported.

Version review: source probe advances to 3 as the earliest affected boundary for
new operation dependency/persistence contracts. Other editorial versions remain
unchanged; generated editorial decisions and prompts were not tuned. Subtitle
preparation version is 3 for the revised matching rules and completed-run reuse.
Reviewed narration is a separate `review_narration` operation at version 1.

Verification evidence: `testing-grounds/architecture-verification-2026-09-06/`.
The packaged rehearsal replays preserved real-source outputs into eight immutable
operations, recomputes the deterministic guides, and then resumes with the concrete
hosted executor while all API requests are forbidden. This verifies the new
contracts against real artifacts; it is not a new hosted editorial-quality run.
Incremental paid verification cost for this pass is zero. All 543 backend tests,
Ruff, mypy (28 modules), 202 frontend tests and frontend quality pass. The final
packaged EXO round trip applied 90 cuts while honoring retained narration, generated
30 real source frames, and reused all review results on a second import. Its report
renders with all 30 images and no horizontal overflow; the test server was stopped.
The installed portable SHA256 is
`FA6D73532C1A972847E629316EADC5708A36A6BAE1EB48A468BE997042F1BD23`.
See the verification directory's `VERIFICATION.md` for methodology and limitations.

Scope boundaries: this is code-composed execution for the existing flows, not a
user-facing graph system. The project index, result browser, explicit history UI,
and automatic clip selection/rendering remain outside this implementation. Typed
transcript and timed-text contracts coexist with schema-validated editorial payloads;
there is no attempt to flatten every domain result into a single generic payload.
The current editorial display cost describes the selected canonical stage results;
archived failed/superseded attempts supply evidence for future project-wide billing
history. It is not yet a unified all-attempts project cost display.

## Real-source verification checkpoint (2026-09-06)

The supplied 79-minute YouTube recording now has one durable hosted transcript
reused by both normal subtitles and the full factual editorial workflow. Normal
hosted/local subtitle workflows accept `cleanup.backend: none`; editorial CLI
start/run accepts repeatable `--transcript-artifact` inputs and rejects changed
completed revisions unless transcription is explicitly restarted.

The real run exposed and fixed two defects: checkpoint loading could overwrite
project narration with source-level derived data, and voice-gap planning could cut
voice omitted by transcript segmentation. Completed global narration now survives
reload; guide planning explicitly consumes saved acoustic speech intervals and
unions them with transcript speech before applying the existing handles.
Transcription is version 14 and action planning version 12. The narration repair
restores a projection from unchanged authoritative output and does not invalidate
paid analysis. Other boundary versions are unchanged.

All 537 backend tests, Ruff, mypy (25 modules), 202 frontend tests, and frontend
quality pass. Packaged runs reuse the same transcript with ASR forbidden; a final
completed editorial resume also passes with every API request forbidden. The final
EXO has 216 cut guides, 84 display subtitles, and six narration briefs; none of the
cuts overlap saved acoustic detection or protected transcript speech. The HTML
renders without overflow or missing images. This remains a human editing guide;
applying every initial silence cut alone retains about 39 minutes, not the brief's
8–12 minute target.

Recorded cumulative paid verification: transcription $0.30916949 of its $10 cap;
editorial $0.45219020 of its separate $10 cap. Earlier attempts are included.
Preserved evidence, iteration comparisons, and final artifacts are in
`testing-grounds/editorial-source-reuse-2026-09-06/VERIFICATION.md`.
Final rebuild/install SHA256: `71859CEC1B42EBB10D9C6E7042AEF987991585DB8F52939349E6DD597F09498F`.
No commit or push has been made.

## Previous checkpoint: operation recovery and source acquisition

- A versioned operation store now persists exact inputs, result revisions, integrity
  digests, and usage within a run. Corruption stops recovery; failed operations retain
  known billing and only completed operations are reused.
- Transcription is saved before subtitle cleanup. Subtitle layout/chapter changes
  invalidate planning, while export/B-roll changes preserve completed preparation.
  Pending checkpoints now use schema 2 and preparation version 2. Local model files
  are checked only for operations that still need to run.
- B-roll uses separate catalog, requirements, proposal, reviewed proposal, and web
  discovery results. The original catalog survives the desktop's description edits;
  a changed source file is rejected. Review interruption no longer silently removes
  B-roll. Provider creation is lazy and skipped when every model result is reusable.
- Silence and B-roll processing accept decisions/callbacks without desktop transport.
  The desktop has an explicit fresh-run choice. Source inspection and speech-selection
  operations are independent of the hosted executor; shared evidence types live in
  `evidence.py`. Default editorial outputs remain unchanged, so the existing editorial
  boundary vector stays unchanged (transcription 13, source probe 2, action planning 11).
- The unused local long-stream workflow/config/launcher are removed. Backend and
  frontend workflow catalogs centralize capabilities. The desktop selects the desired
  output before processing location; existing supported workflows remain composed.
- Source acquisition is extracted from library admission. Both input flows accept a
  finished recording URL, download the full source, persist origin/range metadata, and
  pass the local file to existing processing. Library downloads retain their bounded
  policy. Cancellation and application shutdown stop the downloader process tree.
  Downloads now expose per-file progress, use bounded HTTP requests, and explicitly
  decode UTF-8 across pipe chunks to preserve Japanese titles and paths.
- Automatic highlight selection/clip rendering, general project workspace UI, and
  immutable revision history for all editorial boundaries remain future work. The
  URL flow currently feeds subtitles or factual editing guides, not an auto-clip preset.

Verification: 533 backend tests, 202 frontend tests, Ruff, mypy (25 modules),
and frontend quality pass. Rebuild and installation completed; installed portable
SHA256 is `56309C8A12CA089A330D6A039224C362DD935A2FC3AB6D5062192E9C7591DE5D`.
Nine generated-audio and recovery tests pass against the packaged backend. The
separate-process interrupted-review/export check passes, and the ASAR contains
the acquisition service, shared process lifecycle, and workflow catalog.
Provider responses in tests are synthetic. A real recording, YouTube
`8duZREdR0HQ`, was downloaded in full through the acquisition operation:
5,140,688,226 bytes, 4,766.028 seconds, 2560x1440 at 60 fps, one video and one
audio stream. Shared inspection and beginning/middle/end FFmpeg decoding pass;
the source manifest preserves its Japanese title and origin. Inspection evidence
is under `testing-grounds/real-source-verification/`. Real downloader progress
was also checked with a bounded test transfer, and the packaged acquisition
modules match the verified compiled code. Windows directory listings can show
zero bytes during an open transfer; use progress events or file-handle size for
diagnostics instead. No live hosted calls, paid model execution, commit, or push
occurred. The latest changes are frontend-only; backend verification remains
the previous successful pass.

## Third implementation checkpoint

- Silence review uses a shared durable request/response exchange, with a stable
  review ID and exact input revision. Validation happens before decisions are
  saved; stale or malformed responses cannot authorize media edits.
- Silence execution accepts explicit decisions and has no desktop transport.
- An interrupted silence-review run saves its complete subtitle plan, aligned
  tokens, chapter/QA markers, prior API usage, and a dedicated transcript snapshot.
  Starting the same source/output/settings resumes without ASR or cleanup.
  Source identity, glossary, configuration, text policy, preparation version, and
  explicit transcript input determine the match. Completed runs do not reuse
  pending state. `--fresh-run` intentionally bypasses it; no-sidecars disables it.
- This is bounded pending-run recovery, not general dependency caching. Unsaved
  screen edits, B-roll planning stages, partial transcription, and interrupted
  cleanup do not yet resume. Local model validation still runs before recovery.
- No editorial stored behavior changes in this checkpoint; transcription remains
  version 13. Future source preparation/subtitle behavior changes must also bump
  `PREPARATION_VERSION` in `subtitle_checkpoint.py`.
- Verification includes interrupt/restart through the actual CLI adapter with
  ASR and cleanup forbidden on restart, plus stale/invalid decisions, replaced
  dependencies, snapshot independence, corrupt token timing, cost preservation,
  and completed-run invalidation.

Verification: 530 backend tests, Ruff, and mypy (19 modules) pass. The rebuild
and installation completed with SHA256
`51792FE86DF86166BF93C3FF87564BBD9F585092460BCF2F78244DD4B626975B`.
All six generated-audio and review-recovery tests pass against the packaged
backend. A separate two-process check interrupted review and then accepted the
cut and exported EXO, using generated media, a supplied transcript, and a cleanup
double (cleanup forbidden during resume). Evidence is under
`testing-grounds/workflow-review-resume-check/`. No paid calls or push occurred.
Frontend source did not change in this checkpoint; its prior 197 tests and
quality pass remain applicable. Desktop close/reopen interaction itself remains
an optional check during normal use.

## Second implementation checkpoint

- `media_export.py` owns source layout and EXO export requests. `broll_stage.py`
  owns provider lifecycle and B-roll planning inputs. The subtitle workflow now
  composes these operations and owns rendered-media cleanup in one finalizer.
- `transcript_document.py` persists the full normalized backend result, including
  aligned tokens, selected fine speech regions, raw VAD, settings, and source
  identity. Subtitle plans reference the input transcript revision.
- Transcription publishes neutral speech-activity callbacks; the workflow owns
  silence-review event formatting. Reused transcripts publish the same activity.
- `--transcript-artifact` explicitly reuses a complete transcript. Reuse verifies
  audio track, source modification time, and the existing sampled fingerprint.
  No implicit cache policy or model rerun is introduced. Diagnostic CSVs are no
  longer read by editorial analysis or selected-subtitle alignment.
- Editorial transcription version advances to 13 for the expanded stored contract.
  Source fingerprinting was extracted unchanged into `media_identity.py`.
- The generated-audio integration test transcribes once, deletes CSV exports,
  and produces an EXO through a separate workflow using the saved transcript.
  It asserts that no extraction or model construction occurs on reuse.
- The user has granted standing permission to rebuild throughout this conversation.
  Rebuilds are verification steps, not permission or stopping boundaries. Pause
  only for a concrete unresolved decision or a specified manual product test.

Verification: 526 backend tests, 197 frontend tests, Ruff, mypy (16 modules), and
frontend quality pass. Rebuild and installation completed. Both generated-audio
integration tests also pass when importing the packaged backend. No paid model
calls or push occurred. The user passed the AviUtl import/rendering check of
`testing-grounds/workflow-migration-check/01-uncut.exo` and `02-silence-cut.exo`:
both show the test pattern with the teal panel at upper right; captions
align with the two tones, and the cut version removes about 2.1 s between them.
The retained 0.2 s speech margins and untouched silent tail are intentional. Encoding, paired layers, muted duplicate audio, and both animation
filters per subtitle have been checked programmatically.

Remaining: persistence for subtitle/other operation outputs, durable review
requests and run state, dependency-aware execution, further editorial operation
extraction, and the project-oriented UI. Explicit transcript reuse is operational;
it does not yet provide automatic cross-workflow caching or resume after review.

## First implementation checkpoint

- `aviutl_subtitle.py` is now a CLI adapter. `subtitle_workflow.py` composes
  transcription and subtitle planning through explicit requests; `workflow_policy.py`
  selects raw text and chapter behavior for existing modes.
- `transcript_workflow.py` composes the same operations for editorial ingestion.
  It does not launch a subtitle subprocess or render a disposable EXO. Partial
  results stop before planning, and failure metadata retains incurred API cost.
- `timed_text.py` stores text and source timestamps together in an atomic,
  validated JSON document. Both editorial readers use this contract. Token and
  VAD CSV handoffs remain explicit follow-up work.
- The document identifies its revision, source path, audio track, text policy,
  and completeness. It is a subtitle plan, not the full aligned transcript or a
  content-addressed cache. Source fingerprints and pipeline versions still live
  in the enclosing editorial checkpoint. Full provenance, immutable revision
  storage, and dependency-based reuse remain for the durable-runner milestone.
- Editorial transcription version advances from 11 to 12 because the stored
  handoff changes. Factual prompts and raw subtitle planning policy are preserved.
- CLI compatibility wrappers, the unused pipeline-script option, duplicate raw
  subtitle planning code, and CSV/text pairing readers have been removed.
- No UI redesign, paid model evaluation, or push is part of this first
  implementation checkpoint. The user approved the rebuild after verification.

Verification: Ruff and mypy pass using `.venv-win`; all 522 backend tests and
197 frontend tests pass, along with frontend quality checks. Generated audio
exercises real FFmpeg through both the subtitle CLI and direct editorial
transcription, with model calls replaced by test doubles. The approved
`rebuild-and-install.ps1` completed and installed version 0.1.7 at
`C:\tools\personal\Subtitler-latest\SubUtl.exe`. Packaged backend imports and the
updated editorial CLI contract pass using the project Python environment.
Interactive desktop use and live hosted-model quality remain untested.

## Scope and decisions

- Preserve useful current behavior, not historical APIs, checkpoint formats, or
  configuration compatibility. The sole user has no incomplete projects.
- Retain Electron and Python. Python owns processing and eventually durable run
  state; Electron owns desktop integration and purpose-built review screens.
- Extract operations and recompose existing modes together. Keep the application
  usable after each milestone. No user-facing graph editor or plugin framework.
- Keep editorial behavior changes separate from structural extraction. Review
  the editorial version vector at every stored boundary affected by a change.
- Do not delete behavior tests merely because they fail after moving code. Move
  their imports/test seams to the owning module; remove tests only when the
  behavior they protect is deliberately retired or covered elsewhere.

## Current execution map

| Entry | Current composition | Coupling to remove |
|---|---|---|
| Local / hosted subtitle CLI | Audio preparation, transcription/alignment, subtitle planning/cleanup, optional silence review/cutting, optional B-roll, EXO | CLI owns execution; stages read CLI context; backend emits workflow-specific review events |
| Hosted long-stream desktop / editorial CLI | Probe, per-source transcription, visual learning, factual event mapping, source synthesis, project synthesis, display subtitles/guides, assets, HTML/EXO | Transcription launches subtitle CLI and reads presentation sidecars; executor receives a mutable project and all prior outputs |
| Apply reviewed editorial cuts | Read reviewed EXO, validate reserved markers, map source ranges, export paired media/subtitles | Specialized project paths and source/output time conventions |
| Media library | Scan/probe/index, thumbnails, analysis, web acquisition | Desktop services combine acquisition with library admission requirements |
| Reserved local long-stream | Unavailable | Not a new workflow to implement |

## Operation catalog and target composition

Operations own meaningful transformations, not individual helper functions.
VAD, provider recovery, alignment, and seam reconciliation remain internal to
transcription initially.

| Operation | Explicit inputs | Output |
|---|---|---|
| Acquire source | Local path or URL; acquisition options | Source media with origin and acquired range |
| Prepare audio | Source media; selected track; workspace | Prepared audio with source timeline association |
| Transcribe | Prepared audio; glossary; model/VAD/alignment settings | Timed transcript, speech activity, completeness and usage |
| Plan subtitles | Timed transcript; glossary; formatting/cleanup policy | Timed subtitle document and optional QA/chapter markers |
| Review silence | Speech activity; source duration; user decisions | Accepted source cuts |
| Plan B-roll | Subtitle document; media catalog; user decisions | Asset placements |
| Export EXO | Subtitle document; media placements; layout settings | EXO artifact |
| Learn visuals | Source media; factual transcript/context; knowledge snapshot | Visual evidence and knowledge update |
| Map events | Timed evidence; source facts; objective | Factual source events |
| Synthesize story | Source event artifacts; objective | Factual project story and narration possibilities |
| Prepare editing guides | Story; timed transcripts | Selected display subtitles and deterministic guides |
| Resolve references | Guides; source media | Verified frame assets |
| Import reviewed edit | Original export revision; edited file | Review decisions and source/output timeline map |

Local and hosted subtitle workflows share the same composition and choose model
settings independently. Long-stream uses the same transcription and raw subtitle
planning operations, then the existing factual editorial operations. Its raw
subtitle plan remains the evidence segmentation baseline during extraction.

## Artifact contracts

Persisted artifacts will have a type/schema version, unique revision identity,
exact input revision references, producing operation/version, relevant settings
fingerprint, model/prompt provenance, completeness, usage, and validated payload.
Media stays in managed files; JSON holds references rather than audio samples.
Diagnostic CSV/text files are exports, not canonical communication contracts.

Transcript and event coordinates refer to the original selected source timeline.
Every range identifies its source and units. Edited timelines require a separate
mapping artifact, including source offsets for partial downloads and paired media.
Factual observations, proposed edits, and approved edits are distinct types.

Artifacts are immutable revisions. A reviewed result creates a successor revision;
only dependent operations become stale. Cache matching includes actual input
revisions, relevant settings, and operation behavior versions. An invalid or
partial result must not masquerade as a reusable complete artifact.

Review waits must eventually be persisted, with a review ID and the exact input
revision. Closing the desktop app must not discard pending decisions. Runtime
resources, progress callbacks, cancellation, credentials, and temporary files are
execution services, not artifact fields.

## Milestones and verification

1. Extract typed operation requests and explicit subtitle policies; move CLI
   orchestration into the package; remove compatibility wrappers. Preserve EXO,
   raw long-stream subtitle segmentation, provider cleanup, and estimate-only
   behavior. Run the backend suite, including generated audio through real FFmpeg.
2. Replace editorial subprocess/sidecar transcription handoff with a direct
   composition and validated durable transcript contract. Preserve factual
   evidence segmentation and reject unresolved transcription before analysis.
3. Extract remaining subtitle media/review/export operations and editorial
   operations. Introduce a small durable runner and immutable artifact store while
   migrating these compositions; do not add an unused parallel framework.
4. Replace feature-specific live review handoffs with persisted review requests;
   route desktop and CLI through the same authoritative run state. Verify restart,
   cancellation, stale decisions, and dependency-specific invalidation.
5. Complete existing-mode migration, remove superseded paths, then build the
   project workspace and URL-to-clips flow from the extracted operations.

The foundation is complete when one transcription supports two workflows,
pending review survives restart, changing downstream choices reuses transcription,
and existing supported workflows pass their behavioral checks. Each significant
milestone is a rebuild approval boundary. No paid experiments or pushes before
the applicable project authorization requirements are met.
