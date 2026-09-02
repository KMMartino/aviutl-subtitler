# Implementation status

## Active product contract

The hosted long-stream workflow is a human editing assistant. It analyzes the complete
recording, but it does not decide what gameplay or speech should be removed. Its current
contract is defined in [`human-information-baseline.md`](human-information-baseline.md).

The active pipeline provides:

- full transcription and aligned spoken evidence;
- dense visual and acoustic observations;
- persistent, bounded knowledge associated with the user-selected game or title;
- factual semantic spans, atomic events, longer activity phases, and cross-file context;
- project-wide factual synthesis, long-horizon threads, and selective narration possibilities;
- selected, cleaned on-screen subtitles plus visible full-utterance guides;
- deterministic `[CUT]` markers for voice-free gaps of at least two seconds; and
- JSON, HTML, and EXO artifacts with resumable, versioned checkpoints.

The user-edited EXO is authoritative. A user may move, resize, duplicate, or delete an exact
`[CUT]` object on the reserved cut layer. Reimport applies those reviewed ranges directly.
Retained narration markers invalidate overlapping cuts only during that reviewed reimport.
Narration review creates a separate two-column HTML report with factual events and illustrated
candidate footage; it does not replace narration markers with large EXO text dumps.

## Source and checkpoint handling

- A project may contain multiple chronological source files. Each source is processed and
  checkpointed independently, then carried into the next source as ordinary context.
- A logical source may be a normal recording or a synchronized gameplay/facecam pair. Paired
  projects analyze gameplay video, transcribe facecam audio, and emit both media streams in the
  correct AviUtl layer order.
- Fingerprints verify source identity independently of filenames and timestamps. Compatible
  artifacts can be resumed from the earliest matching boundary.
- The durable boundaries remain source probe, transcription, visual learning, semantic spans,
  local reconciliation, global reconciliation, action planning, and editorial assets. The last
  boundary is retained for checkpoint compatibility and now represents dashboard/output
  preparation for ordinary human-information runs.
- The semantic boundary stores factual observations only. It no longer generates provisional
  edit recommendations, narration, effects, or targeted visual adjudication.
- Partial semantic-window progress includes the prompt version, preventing an interrupted run
  from reusing windows produced under a different semantic contract.

## Output structure

- Selected subtitles use the stream-oriented compact style and remain aligned to source speech.
- Utterance objects expose the complete aligned thought for human reference and may extend into
  the small 50 ms leading and 100 ms trailing speech handles adjacent to generated cuts.
- Event objects use concise factual labels at multiple scales. Machine IDs, confidence values,
  reasoning, and causal arrows are not displayed.
- Narration possibilities are intentionally sparse. The portable guidance is documented in
  [`narration-practices.md`](narration-practices.md); runtime behavior never depends on the
  downloaded reference videos or any earlier FF16 example.
- The HTML dashboard summarizes factual progression, threads, phases, narration possibilities,
  selected subtitle counts, source state, and end-to-end hosted cost without listing every VAD
  gap or presenting retired editorial recommendations.

## Retired experiments

The following are not active product behavior and should not be reintroduced through stale
configuration, prompts, or tests:

- high-activity-only long-stream transcription;
- optional long-stream editorial analysis;
- transcript-based automatic cut nomination;
- visual veto/adjudication of proposed cuts;
- canonical automatic edit actions, duration-target planning, montage recommendations, creative
  accents, or final director reconciliation;
- automatic B-roll/reference-image selection during the initial run; and
- EXO narration markers replaced by generated brief text.

Older checkpoints may still contain fields from these experiments. Readers tolerate those
fields so existing projects remain recoverable, but regenerated active artifacts do not depend
on them.

## Remaining product validation

Use ordinary projects rather than reference-specific fixtures to verify:

1. single-file, multi-file, and paired gameplay/facecam source handling;
2. interruption and compatible restart at every paid boundary;
3. factual event accuracy at opening, middle, ending, and cross-file transitions;
4. selected subtitle density, timing, cleanup, wrapping, and utterance-guide readability;
5. exact initial `[CUT]` placement and authoritative reviewed-EXO reimport;
6. narration marker compatibility for both `ナレーション` and `[ナレーション]`;
7. narration-review HTML timing, two-column layout, and representative images; and
8. final EXO encoding, contractual layer order, dashboard rendering, and reported hosted cost.

The future horizontal AviUtl-shaped event timeline and other deferred concepts remain in
`plans/future ideas/`.
