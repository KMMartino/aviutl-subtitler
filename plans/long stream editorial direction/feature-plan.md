# Feature plan: long-stream editorial map

## Product goal

Given one or more gameplay recordings or general stream recordings, produce a detailed, non-destructive editorial map that helps a creator make a coherent long-form video. It should preserve compelling uninterrupted material, identify stale or redundant material, connect separated parts of the same throughline, and propose places where new narration could compress or clarify events.

Version 1 never changes the source media or creates an automatic edit.

## Two editorial priors

The planner must model two directions of editing explicitly. They can produce superficially similar cut decisions, but they preserve different qualities and reach very different durations.

### Continuity-first: remove what fails to earn its time

Start with the recording intact. A section remains unless there is affirmative evidence that removing or shortening it improves the experience.

This prior protects:

- the feeling of being present for a run or conversation;
- gradual tension, exploration, and environmental understanding;
- unplanned humor and personality;
- meaningful silence, concentration, and anticipation;
- continuity around encounters and topics.

Its main operations are trims, dead-time removal, tightening repeated setup, and selective condensation of clearly redundant passages. This is the default prior for the original lightly edited long-stream concept.

### Selection-first: include what earns a place

Start with an empty final timeline. Add the moments needed to communicate the premise, progression, most valuable experiences, and satisfying payoffs. Add connective material or narration only where the selected moments would otherwise be confusing.

This prior is useful when the requested runtime cannot be reached through safe subtraction alone. It favors milestones, representative attempts, explanations, reactions, demonstrations, and payoffs. The FF16 sample is strong evidence for this end of the spectrum, but not for the overall default.

### Why they are not one score

“Value if included” and “damage if removed” are related but not opposites. For example:

- a quiet approach to a boss may not be a highlight, but removing it may damage anticipation;
- an isolated spectacular attack may be attractive to include, but contribute little to the throughline;
- an equipment screen may be visually weak yet essential proof of a challenge constraint;
- the thirtieth attempt may contain a valuable reaction while most of the attempt is redundant.

The analysis must therefore retain separate evidence for both questions rather than reducing every span to a generic keep score.

## User flow

1. The user selects the long-stream editorial analysis option. Hosted analysis is required.
2. A multi-file picker accepts recordings in intended chronological order and shows total duration.
   A chronological item may resolve to either one ordinary recording or a synchronized facecam/gameplay pair. For a pair, voice transcription uses the facecam member and visual analysis uses the gameplay member; the pair is still one source for duration budgeting and cross-file context.
3. The user enters required project context:
   - title/game;
   - objective or premise of the recordings.
4. The user may add must-keep moments and subjects to de-emphasize.
5. A dual-ended slider selects an acceptable final-runtime range. The interface shows durations, not a compression ratio.
6. The app estimates cost and explains that billing is driven by speech, sampled frames, and model analysis rather than total runtime alone. The run cannot exceed the configured hard ceiling without explicit confirmation. The initial proposed ceiling is `$10 per source hour`; normal estimates should be much lower.
7. The app analyzes one source file at a time, checkpoints after every durable stage, and updates overall progress.
8. The result opens as a readable HTML editorial report and can also be exported as JSON and layered EXO markers.

## Serial multi-file processing

The UI presents one project, but the processing unit is one source file.

For file `N`:

1. fingerprint and probe the source;
2. transcribe all detected speech;
3. generate economical visual samples and locally derived signals;
4. build observations and candidate semantic spans;
5. reconcile those spans with transcript evidence;
6. update threads, attempt clusters, entities, objectives, and open questions;
7. create a durable per-file result and a compact cumulative context snapshot;
8. pass the snapshot and selectively retrieved prior evidence into file `N+1`.

File boundaries are storage and recovery boundaries only. A boss attempt at the end of one file and its successful retry at the start of the next must be linkable exactly like two distant spans in one file.

After all files complete, a global reconciliation pass applies the duration target, resolves competing recommendations, and creates cross-project narration briefs and connections.

## Persistent state

The canonical project artifact should contain at least:

```text
project
  project_id
  schema_version
  title_or_game
  objective
  target_duration_min_ms
  target_duration_max_ms
  must_keep_notes[]
  de_emphasize_notes[]
  ordered_sources[]
  run_provenance

source_result
  source_id and fingerprint
  media_probe
  transcript_chunks[]
  visual_observations[]
  semantic_spans[]
  attempt_clusters[]
  local_threads[]
  uncertainties[]
  processing_checkpoints

cumulative_context
  current_objectives[]
  completed_milestones[]
  open_threads[]
  recurring_locations_entities_mechanics[]
  known_repetition_patterns[]
  creator_stance_and_sentiment[]
  retrieval_index

editorial_map
global_threads[]
  recommendations[]
  narration_briefs[]
  connections[]
  conflicts[]
  duration_budget
  editorial_direction_summary
  optimal_plan[]
```

The cumulative snapshot should be compact enough to pass forward cheaply. Full earlier evidence remains retrievable by stable IDs rather than being inserted into every later model prompt.

## Analysis pipeline

### Stage A: deterministic preparation

- source fingerprinting using file size, duration, stream metadata, and sampled hashes;
- audio extraction, VAD, and full speech transcription;
- shot/change, motion, audio-energy, scene-loading, and UI-stability signals where inexpensive;
- time-indexed sample selection with higher density near detected changes and uncertain regions.

Audio energy and silence are features only. They are never cut rules.

### Stage B: visual learning pass

Use a low-cost preliminary pass to learn recording-specific vocabulary and recurring states:

- repeated locations and menus;
- player HUD and game UI;
- recurring enemies, bosses, attempts, loading/retry screens, and rewards;
- likely cutscenes, traversal, combat, configuration, and downtime;
- vlog/information-stream equivalents such as whiteboards, demonstrations, or stable talking setups.

These learned labels are probabilistic and project-local. There are no hardcoded per-game detectors.

### Stage C: multimodal semantic spans

Combine transcript, visual observations, learned states, and temporal evidence into soft spans. A span may represent an objective, tactic, encounter, attempt, location visit, digression, topic, payoff, or transition.

Spans can overlap. Boundaries carry confidence plus suggested handle ranges rather than pretending to be exact edit points.

### Stage D: thread and repetition analysis

Identify local and long-range throughlines. Examples include:

- a boss's repeated attempts and milestones;
- an upgrade that later changes an encounter;
- a topic raised, interrupted, and resumed much later;
- a joke or claim with a later payoff;
- recurrent traversal through already-established space;
- a repeated explanation that can be consolidated.

Thread identity is soft. The model records supporting and contradicting evidence and may mark an uncertain connection for review.

### Stage E: editorial recommendations

Create recommendations at multiple scales:

- micro: trim a pause, menu wait, or repeated setup;
- scene: condense a traversal, routine fight, upgrade visit, or attempt cluster;
- thread: concatenate separated discussion pieces or summarize a recurring process;
- project: allocate runtime among objectives and preserve the major causal arc.

Every recommendation includes:

- stable ID and source range(s);
- content disposition: `keep`, `condense`, `omit`, `connect`, or `review`;
- presentation mode: `live`, `live_excerpt`, `narration_over_source`, `narration_montage`, or `narration_bridge`;
- reason and expected viewer benefit;
- confidence and evidence IDs;
- soft head/tail handles;
- estimated kept-duration range;
- dependencies and conflicts with other recommendations;
- alternative conservative recommendation where appropriate.

Every analyzed span should additionally retain:

- `continuity_case`: what experiential, causal, comedic, dramatic, or explanatory value would be lost if the span disappeared;
- `subtraction_case`: what is stale, duplicated, or costly about preserving it in full;
- `selection_case`: what specific material earns inclusion in a shorter construction;
- `context_dependencies`: which setup, outcome, or narration would be required if only the selected material survives.

These fields are evidence for planning, not four competing user-facing markers. The HTML can summarize them behind the recommended action.

The final director does not expose these candidate labels directly. It converts selected material into one canonical primary operation—preserve, trim, cut, extract highlights, montage, narrated montage, narration bridge, connect ranges, reorder ranges, or manual review—and may attach explicitly linked canonical accents or continuity edits. Natural-language wording may vary, but the operation type does not.

After the director pass, the application resolves conflicting primary ranges into a single non-overlapping plan, inserts preserve actions for every uncovered interval, and joins adjacent preserves. This makes “no edit” explicit without asking the model to manufacture routine keep instructions.

Visually dull but new material defaults to `review`, not `omit`, unless transcript and wider context provide affirmative evidence that it is redundant.

### Stage F: narration briefs

Narration is assumed not to exist yet. The output is a brief, not a finished imposed script:

- memory-jogging event summary;
- bullet points the creator may cover;
- reason the block belongs in the final story;
- allowed interpretation or foreshadowing;
- factual source citations;
- suggested representative visuals or montage candidates;
- original-audio moments worth punching through;
- uncertainty or fact-check notes;
- approximate spoken-duration range.

### Stage G: global duration planning

The target range is a budget, not a promise. Internally, the difference between source duration and the selected range creates **editorial pressure**. This value does not need to be shown as a compression ratio.

The planner responds progressively:

1. protect explicit must-keeps, causal prerequisites, unique payoffs, and high-confidence meaningful silence;
2. apply high-confidence continuity-first removals such as waits, duplicated setup, technical interruptions, and materially identical repetition;
3. tighten scenes while preserving their live shape;
4. convert suitable scenes or attempt clusters into representative excerpts;
5. use selection-first construction and narration briefs for lower-priority threads when the target still requires it;
6. report that the target is editorially unsafe if meeting it would require removing protected material or inventing unsupported context.

The blend is local rather than global. A single project may preserve a boss fight almost continuously, treat traversal with continuity-first cleanup, turn repeated upgrade visits into a narrated montage, and represent an unimportant side thread with one selected exchange. This produces a more coherent result than applying the same percentage reduction to every scene.

The planner must offer one internally consistent proposal within or near the range. Continuity-led and selection-led reasoning are internal priors blended independently for each scene; they are not parallel user-facing alternatives. The editor should receive the single best operational action at each location, not an upper-bound and lower-bound instruction for the same material.

Projects already below 12 hours can default to lighter cleanup. Projects over the target should first remove clear redundancy, then condense repeated processes, and only then propose narration-led compression of meaningful content.

The slider must not silently make narration-heavy editing inevitable. If the lower bound demands a selection-first result, the UI/report should say that the requested duration requires substantial reconstruction and new narration.

## Outputs

### JSON

Canonical, checkpointable, schema-versioned artifact. It retains provenance, evidence references, per-stage completion, model/prompt identifiers, costs, and source fingerprints. Prompt/model versions should not prevent resume; they should trigger a compatibility warning and selective re-analysis when necessary.

### HTML

Primary human-readable output:

- overview and duration budget;
- one authoritative direction list linked to chronological action cards;
- a timeline rail showing every action's project position and start/end time;
- action-family colors plus a separate shared color for each distant thread;
- a primary-action column and an explicitly linked supporting-edits column;
- narration briefs inside narrated primary actions;
- verified source-frame crops beside reference-dependent supporting edits;
- filters by recommendation type, confidence, source, and thread;
- visible overlaps/conflicts instead of silently resolving them.

The HTML should be self-contained or packaged with a predictable adjacent data file so it can be reopened without the app.

### EXO

The source video/audio objects are split at the authoritative primary-action boundaries before export. Every action interval, including an explicit preserve, receives a small direction number grouped with the corresponding linked media chunk; that number matches the HTML editorial list and remains synchronized as the editor moves or removes the chunk. Primary prose stays in HTML rather than being duplicated in EXO; EXO text markers are reserved for concrete local accents.

Suggestion markers only. Preserve overlapping ideas on separate layers rather than flattening them prematurely. Proposed semantic layers:

1. normal referenced media as required by the composite format;
2. keep/live anchors;
3. cut or condense candidates;
4. narration briefs;
5. thread connections and chapters;
6. uncertainty/conflict markers.

Final layer numbers must respect the repository's existing EXO invariants when implementation begins.

## Checkpointing and recovery

- Write an atomic checkpoint after each stage and after each source file.
- Record a manually maintained version at every durable pipeline boundary. Compare the ordered version vector on resume and invalidate the first mismatched boundary plus every downstream artifact, while retaining all compatible upstream outputs.
- A restart-from-checkpoint action accepts the project artifact.
- Resolve original media automatically from recorded absolute and relative paths.
- If unresolved, prompt the user to locate each source and verify its fingerprint before resuming.
- A renamed or moved matching file is acceptable; a fingerprint mismatch blocks reuse of time-indexed analysis until explicitly reprocessed.
- Keep partial model responses and completed source results. Retry only missing or invalid work.
- Record cost and request IDs per stage to avoid accidental duplicate spending.

## Cost policy

Use `$10 per source hour` as a hard safety ceiling, not an expected price or allocation. Estimate and report separate components:

- transcription based on detected speech duration;
- vision based on actual sampled frames and detail level;
- text reasoning based on transcript/context tokens;
- optional retry reserve.

Sampling should be adaptive. Start coarse, increase density around changes, attempts, uncertain boundaries, and high-value candidate moments, and reuse cached observations. A full second "proper pass" should mean targeted refinement over the learned state, not blindly analyzing the entire video twice.

The FF16 evidence run cost roughly `$0.55` for one narration transcript and simple visual passes over about 107 minutes of gameplay. This is not a production estimate—the sample used sparse visual analysis and did not perform the proposed semantic or global planning passes—but it suggests the ceiling has substantial safety margin.

## Delivery phases after approval

### Phase 0: artifact contract and fixtures

- define schema, IDs, fingerprints, checkpoints, and migration policy;
- build parsers/renderers for JSON, HTML, and EXO suggestion layers;
- create synthetic fixtures plus the FF16 evidence fixture.

### Phase 1: one-file gameplay vertical slice

- full transcript;
- adaptive frame sampling;
- learned project vocabulary;
- semantic spans, attempt clusters, and narration briefs;
- separate continuity, subtraction, selection, and context-dependency evidence;
- suggestion-only HTML/JSON/EXO output;
- resume after an interrupted vision stage.

### Phase 2: serial multi-file projects

- multi-file picker and duration-range UI;
- cumulative context snapshots and retrieval;
- seamless cross-file threads;
- final global reconciliation.

### Phase 3: general/informational streams

Reuse preparation, persistence, thread linking, and outputs. Add a divergent analysis profile emphasizing topic changes, claims, examples, questions, resumptions, and repeated explanations rather than gameplay attempts and spatial recurrence. Present gameplay and informational-stream analysis as distinct UI choices sharing the backend.

### Deferred tickets

- Twitch/YouTube chat import and on-screen question suggestions;
- speaker diarization;
- chat-overlay OCR;
- automatic cuts or rearrangement;
- auto-generated montage assembly;
- project privacy/disclosure UI;
- learning reusable detectors across unrelated users or projects.

## Remaining questions that should be answered by a prototype

These do not block artifact design:

- What initial frame density gives reliable attempt/location recognition at acceptable cost?
- How much cumulative state can be summarized before cross-file links degrade?
- Should recommendation overlap be displayed as lanes, stacked cards, or both in HTML?
- How should spoken-duration estimates account for the creator's typical narration pace?
- Which evidence should force `review` when transcript and vision disagree?
- How should editorial pressure be distributed among scenes without sacrificing the project's intended texture?
- At what point should the planner declare a requested lower bound unsafe instead of fabricating a feasible-looking plan?
- What maximum source/project duration remains usable before the HTML needs pagination or lazy loading?
