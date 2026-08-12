# B-roll Architecture

## Product boundary

B-roll is a hosted short-workflow feature. It recommends existing videos or images, identifies unmet media needs, and writes accepted placements into the generated EXO. Source media is never modified.

The first production slice deliberately separates three questions:

1. What media does the user have?
2. What visual would improve this part of the transcript?
3. Is a discovered web source permitted to enter the user's library?

The planner can answer the second question, but it cannot silently answer the third. Web discovery produces unverified candidates. Download and promotion require the user's explicit rights confirmation and a final description.

## Runtime shape

```text
Media Library UI
    |
    v
validated Electron IPC
    |
    +--> MediaLibraryService --> scanner / FFprobe
    |          |
    |          +--> dedicated SQLite worker --> library.sqlite3
    |          |
    |          +--> sampled-frame analysis --> OpenAI Responses API
    |
    +--> managed yt-dlp staging --> rights approval --> web media pool

Hosted subtitle run
    |
    +--> read-only catalog snapshot + final transcript
    |
    +--> B-roll planner --> confidence policy --> optional review
    |
    +--> missing-needs web search (unverified candidates only)
    |
    +--> combined, non-destructive EXO + broll_plan.json
```

The Electron process owns catalog mutation. Python opens the catalog read-only while planning, so a run cannot rewrite library state or descriptions.

## Storage model

The application database is:

```text
<Electron userData>/media-library/library.sqlite3
```

Application-generated and web-downloaded assets use separate managed roots:

```text
<Videos>/SubUtl Media
<Videos>/SubUtl Web Media
```

Web downloads first enter a per-job staging directory below Electron `userData`. Only an approved, completed file is atomically moved into the web root and indexed. Thumbnail and staging caches are outside both indexed roots.

The schema includes:

- `library_roots`: enabled referenced folders plus generated and web managed pools.
- `library_directory_scopes`: explicitly included subdirectories; the root top level is implicit.
- `library_directory_visibility`: persisted subtree and direct-file visibility toggles for the catalog.
- `library_directory_ui_state`: directories hidden from Panel One without changing tracking or catalog visibility.
- `assets`: identity, path, availability, technical metadata (including image alpha-channel status), descriptions, provenance, and analysis state.
- `asset_segments`: absolute millisecond ranges, semantic descriptions, AI/user provenance, and user locks. File-wide descriptions coexist with segments, while segment ranges are kept non-overlapping.
- `asset_descriptions`: versioned filename, AI, web, and user descriptions.
- `analysis_runs`: model, prompt, fingerprint, cost, status, and failure history.
- `asset_search`: FTS5 retrieval over titles, paths, descriptions, and tags.
- `embeddings`, `jobs`, and `web_candidates`: reserved foundations for later semantic retrieval and resumable background work.

User descriptions take precedence over AI and filename-derived descriptions. Rescans retain description history and mark disappeared files as missing instead of deleting their records.

## Indexing and scaling

New roots scan only their top level by default. Panel One is a single File Explorer-style tree with all roots stacked vertically. Subdirectories are opt-in scopes selected explicitly from a right-click menu, and each scope indexes only files directly inside it. Clicking a directory only expands or collapses it. Untracked subtrees are unchecked and muted. Directory and virtual “Files in…” checkboxes control persisted catalog visibility without changing tracking state. Checkbox updates modify the in-memory tree immediately and query only SQLite for the filtered catalog; they do not repeat the filesystem directory walk. Directories may be persistently hidden from Panel One, with a session-only “show hidden” control for recovery and unhiding. This avoids accidentally ingesting frame dumps and generated-analysis directories.

Scans enumerate supported video and image formats, gather filesystem metadata, compute a bounded first/last-chunk fingerprint, and use FFprobe for duration, dimensions, codecs, audio presence, frame rate, and decoded image pixel format. For formats that can carry transparency, the asset details report whether an alpha channel was detected; palette formats that FFprobe cannot disambiguate are reported as unknown rather than guessed. Inspection concurrency is bounded to two files, and a scan containing more than 10,000 supported files fails before per-file probing begins.

Enabled location checkboxes are the catalog and planning filter. A direct-files group can be shown alone, added, or untracked in one transaction. Untracking never changes source files. Application-managed groups additionally expose a separate, explicit filesystem deletion action. Removing a referenced location deletes all of its catalog records without deleting source files. The UI is paged and searchable. A planning run ranks at most 1,000 recent candidate rows and sends at most 100 relevant assets to the model. This keeps a 10,000-file catalog usable without placing the entire library in a prompt.

SQLite is configured with WAL mode. All writes are serialized in a worker thread so scans and analysis updates do not block the renderer. Image and video thumbnails are generated lazily with FFmpeg, cached at low resolution, and exposed through the private media protocol.

## Visual intelligence

Analysis is explicit per asset. Before any request, the user sees:

- the model;
- the number of sampled frames;
- an estimated price;
- the privacy boundary.

Images use one optimized sample and a dedicated confirmation dialog with no video levels, frame terminology, or timeline controls. The four standard video levels share one piecewise-linear duration curve with fixed 1×, 2×, 5×, and 10× multipliers. The exact anchors are:

| Duration | Simple | Medium | Detailed | Precise |
| --- | ---: | ---: | ---: | ---: |
| 2 seconds | 1 | 2 | 5 | 10 |
| 120 seconds | 10 | 20 | 50 | 100 |
| 1,200 seconds | 40 | 80 | 200 | 400 |

Between anchors the base curve is interpolated, and after 1,200 seconds standard modes continue in direct proportion to duration. FFmpeg creates temporary JPEGs; only those frames are sent to the provider, and the original media file is not uploaded. The in-app confirmation dialog shows frame counts and estimated costs for every available level, marks a duration-based recommendation, and defaults to it. Bulk analysis applies the selected level sequentially to enabled, available, un-analyzed assets and can stop after the current asset.

The provider returns an editorial-use summary, retrieval tags, and chronological sample-index ranges. It groups adjacent samples with the same role and distinguishes gameplay, trailers/cinematics, talking heads, UI, effects, art, and unusable sections. Range boundaries are placed between neighboring samples. The prompt avoids incidental frame narration and prioritizes tone, retrieval, and likely use in an edit. The provider interface is neutral, while the first implementation uses OpenAI Responses with `gpt-5.6-terra`.

Probe is a fifth, explicitly selectable mode for efficient long-form analysis:

1. Survey the whole timeline with a duration-scaled set of evenly spaced low-detail probes: 40 at 20 minutes, about 89 at 100 minutes, and never more than 100.
2. Give the coarse analyzer a duration-scaled transition budget: one possible transition per 150 seconds, with a minimum of 16 and an absolute ceiling of 96. The range budget is one higher than the transition budget. A single range for genuinely continuous footage remains explicitly acceptable.
3. Refine only the returned block transitions. Each refinement request batches two images per active interval, at one-third and two-thirds. Each probe can match the left scene, match the right scene, or identify a genuinely new intermediate scene. Adjacent equal regions are discarded from further work; one surviving transition continues as one branch, while multiple detected transitions continue independently, up to the duration-scaled budget.
4. Stop at a duration-scaled target bracket width, from quarter-second precision on very short media to three seconds on long-form media.

Continuous gameplay therefore ends after the survey with no refinement frames. Probe is recommended above five minutes; shorter assets default to one of the standard modes so close edits receive denser coverage. At the exact 100-minute benchmark, the transition budget is 40: a typical seven-transition estimate is about 131 images, while the all-branches-active maximum is about 329. A three-hour stream receives a 72-transition budget, and streams of four hours or more approach the absolute 96-transition ceiling. Both trisection images have already been sent when a round concludes that only one cut exists; “discarding” the unnecessary branch prevents further probes but cannot retroactively remove that round’s API cost.

## Planning and review policy

Planning has two hosted text passes in the normal case and does not visually analyze the primary video. The first pass identifies concrete editorial needs and protected transcript ranges where wording implies that the viewer should inspect the primary screen or a live demonstration. Protected ranges are enforced after the model response. Each need retrieves a small, separately balanced set of indexed videos and images using multilingual search terms; unrelated zero-score assets are not sent to the final editor.

The final pass scores need value, semantic relevance, placement safety, source grounding, technical quality, and intentional small-overlay use independently. Fixed conservative thresholds decide inclusion; the UI exposes only one `Suggest B-roll` switch rather than confidence modes. Density guidance discourages repetitive or back-to-back placements without enforcing a rigid quota.

Filename/path-only assets cannot be accepted silently or discarded merely because they lack grounding. The final editor returns plausible, specific title matches in a separate review-candidate list instead of treating them as placements. The desktop then pauses on a real source preview. The user either rejects the file or supplies an accurate library description, which is saved to the catalog. Planning reruns using approved files as described assets; rejected title-only assets are excluded.

The same review can launch the ordinary Simple, Medium, Detailed, Precise, or Probe visual analysis for the whole file, or for a user-entered source range. Range estimates and sampling use only the selected duration, while stored timestamps remain absolute to the original file. A user may instead save a manual range description. User ranges are authoritative: inserting one trims overlapping AI ranges and conflicts with another user range are rejected. Full AI reanalysis replaces AI ranges only; it never removes user ranges. The paused planner reloads the catalog after review and treats each segment as a distinct `asset_id` plus `segment_id` candidate without creating duplicate media files.

The console summary and auditable `broll_plan.json` report editorial needs, catalog retrieval, filename review decisions, parser rejections, safety omissions, proposed and accepted placements, missing needs, and web candidates.

## Web acquisition

Grounded web search is disabled by default. If explicitly enabled, it is used only for unmet asset needs. Results remain `rights_status=unverified` and are recorded as source-page candidates, not downloaded media.

The Media Library import flow uses a verified app-managed yt-dlp nightly executable in two phases:

1. Inspect metadata without downloading.
2. After rights confirmation and a final description, download into staging.

Sources up to 20 minutes are retained whole. Longer sources use a selected 20-minute window. Audio is preserved in the acquired file. URLs must be public HTTP(S) URLs without embedded credentials.

Runtime settings expose a one-click nightly install/update, an optional Deno executable, and an optional browser-cookie source/profile. The app reuses its existing FFmpeg runtime and invokes yt-dlp with `--ignore-config`, so unrelated user configuration cannot silently broaden behavior.

## EXO composition

For a B-roll run, the generated EXO is self-contained whenever the input has video:

- Layer 1: uninterrupted primary video.
- Layer 2: uninterrupted primary audio.
- Layer 3: accepted B-roll visuals.
- Layer 4: linked B-roll audio objects at volume zero.
- Later layers: subtitle background, normal subtitles, QA/mistranscription markers, and chapters.

Source clips use their own indexed frame rate when converting a requested timestamp to AviUtl's source frame position. Ordinary B-roll uses aspect-preserving cover scaling to fill the project canvas, including enlargement of low-resolution sources. Information-sensitive stills can use contain scaling, while a smaller overlay requires explicit, high-confidence comedic or reaction intent. Images and video clips reference the library files directly; originals are not rewritten.

If an indexed file disappears before planning, it is excluded and reported through the missing-asset path. Planning, web discovery, and Media Library startup are fail-soft so unrelated subtitle generation remains available.

## Next production increments

- Replace the current bounded lexical ranker with hybrid FTS and embeddings when catalog recall becomes a measured problem.
- Move scan and analysis progress into persisted `jobs` for pause/resume and crash recovery.
- Persist grounded discovery candidates in `web_candidates` and expose citations directly in the UI.
- Add video contact sheets for faster chronological-range review.
- Add provider-specific media analysis implementations behind the existing interface.
- Add automated retention for abandoned staging jobs and preview proxies.
