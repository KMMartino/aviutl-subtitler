# Phase 4 implementation checkpoint (internal)

User authorized phase 4 implementation on 2026-09-06 after discussion. No user node editor. Project = session; ordered recordings can be paired gameplay/facecam. Starts aligned. Speech defaults to facecam, gameplay audio remains with gameplay; user explicitly requested configurable roles. Rebuild permission persists. Phase 5 remains parked. Do not require user approval just to continue/rebuild.

Implemented in frontend shared/creatorProject.ts, main/creatorProjectStore.ts, renderer/components/CreatorWorkspace.tsx and App/IPC/preload wiring:
- Managed default Videos/SubUtl/Projects; per-project parent location; recent index in AppData; manifest inside visible project. Existing media referenced in place.
- Ordered recording groups; existing filename pairing plus explicit two-step gameplay/facecam file selection. Independent speechSource selection facecam/gameplay and separate role swap.
- Existing subtitle/editing-guide tasks connected to projects. One-off runs create file-named project. Guide drafts saved serially. Per-result settings.json and request.json.
- Separate result directories; source/parent dependencies, source size/mtime observations, stale status, current results/history. Interrupted result state recovered on open, explicit Resume task uses same request/checkpoint/output and blocks changed sources.
- Transcript reuse across task result folders; Python verifies full source identity. Compatible guide checkpoint + immutable operation store cloned into successor; previous result untouched.
- Transcript viewer, explicit Export EXO to project exports folder, import reviewed EXO against selected guide revision (checks source filenames and passes exact checkpoint). Imported EXO copied into new result folder; parent preserved.
- App workspace grid updated; existing task panels retained inside workspace; no clip features.

Backend: speech_source optional facecam/gameplay stored with EditorialSourceInput; Hosted executor chooses speech file for transcription/acoustic analysis without changing media layer sources. Resume source matching includes speech setting. Earliest editorial boundary source_probe bumped 3->4 before tests. Fixed transcript document lookup to prefer document_path on supplied transcript resume. Consolidated duplicate resume source matching via _source_matches.

Verification: 543 backend tests, Ruff, mypy 28 modules; frontend quality and 208 tests. Browser component smoke with actual React component (mock desktop bridge): speech source persists through close/reopen, no overflow. Packaged actual desktop API smoke: real 3s generated FFmpeg source inspection, create/save/open project, stale edit rejected, real renderer starts without errors/no overflow. Stored in .worklogs/phase4-*. Last rebuild log .worklogs/phase4-rebuild.log; verify newest hash before final handoff. No hosted API calls this pass; cumulative prior cost remains $0.7613596859375.

Remaining limits / further work:
- Paired exporter still requires lengths within ten frames. Nonzero sync offsets and unequal-length support need timeline + export work, not just UI controls. No offset control implemented.
- No copy/relink media manager, project portability/move repair, or URL downloads inside active project's media directory (existing source acquisition root retained).
- Global task settings reused; snapshots preserved per result, but only recording/project editorial draft changes drive visible stale indicators.
- Transcript viewer is read-only; no general transcript editor. Subtitle task operates on selected recording; guide takes ordered session sources.
- UI labels in new workspace English, existing task panels retain localization. No product-wide translation pass done.
- Standalone project graph is dependency records plus existing operation graph, not a general graph scheduler rewrite.

Temporary servers/processes to stop: Vite port51930 launched cmd PID16788 (find owning node port to stop). Packaged smoke port51931 app first PID10800 was stopped; any subsequent launch must be stopped too. Browser tab_5 points to Vite .worklogs/phase4-preview.html. Latest renderer fixture is ignored frontend/.worklogs. Packaged test user state isolated .worklogs/phase4-app-state. Do not affect user's regular app/settings. Generated smoke media .worklogs/phase4-source.mp4. Failed first fixture human-information-preview-source.mp4 was an existing dummy file, not app regression; valid generated source passed.

Final verification completed 2026-09-06:
- Installed executable C:/tools/personal/Subtitler-latest/SubUtl.exe, 104746923 bytes, SHA256 D436EDDEE4FE95717A5EFA42499A22796815A534111299A3DB7B6A24591BD85A.
- Final packaged smoke revalidated create/save/open/reject-stale-save using real generated media. Actual renderer project open checked at 1280x800, no horizontal overflow. Task area 436px tall. Fixed clipped task controls with scrolling and added Hide project details. Screenshot .worklogs/phase4-packaged.png.
- 543 backend tests, 208 frontend tests, all applicable quality checks passed. No hosted spend. Temporary test app PID33140 and Vite owning node25168/cmd16788 stopped.
- Added explicit Resume task recovery from saved request.json; retains existing output/checkpoint and reviews instead of creating a new result. Added in-app read-only transcript modal and Export EXO into exports folder. Source missing/changed observations persisted and mark results stale.
