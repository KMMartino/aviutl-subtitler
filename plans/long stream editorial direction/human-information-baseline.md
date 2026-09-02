# Human-Information Editorial Baseline

Status: implemented in source; desktop product validation remains ongoing.

## Product contract

The long-stream workflow analyzes the complete recording and exposes evidence to a human editor. It does not propose cuts, preserves, montages, creative accents, or duration-driven edit plans. Narration briefs are the sole editorial recommendation.

Retain full transcription and alignment, dense visual and acoustic analysis, persistent game knowledge, semantic utterances, atomic events, cross-file continuity, long-running phases, and setup/payoff relationships. A factual global pass joins these into a rich multi-scale event and story graph.

## Generated EXO guides

- Keep selected display subtitles at the current bottom position. Select roughly two to three times the present density, favoring complete single-thought clusters. Clean only selected text; timing remains aligned evidence. Wrap selected subtitles at 40 characters per line.
- Reuse complete semantic utterance markers as visible guides immediately above selected subtitles. Use raw aligned text, half the selected-subtitle font size, and wrap at 80 characters per line.
- Create a `[CUT]` text object on the dedicated cut layer for every no-detected-voice gap that leaves at least two seconds after the speech handles. Include internal, leading, and trailing gaps. Visual analysis does not reject these markers. A human-reviewed marker may be resized below two seconds and remains authoritative on reimport.
- Treat a reviewed EXO as authoritative. Only exact `[CUT]` text on the reserved layer triggers removal. Applying cuts unions those ranges, slices every overlapping timeline object, preserves the portions outside each cut, adjusts media offsets, and shifts all later objects left.
- Emit factual event nodes below the existing lowest timeline row. Nodes may have arbitrary duration. Pack overlaps into as many lanes as required and color them by category. Display event text in the upper-left near `X=-1200`, `Y=-650`; trial these coordinates before finalizing.
- Represent causal edges only through shared thread numbers and colors on connected nodes. Do not render connector objects or arrows.

## Event graph content

Represent three scales:

1. Long-running phases such as stages, areas, attempts, character creation, build development, and boss battles.
2. Atomic events such as choices, acquisitions, recruitment, state changes, encounters, failures, interruptions, and results.
3. Long-horizon relationships such as an item becoming crucial later, a build choice paying off in combat, or a failure causing a later strategy change.

Event objects contain concise factual labels and descriptions. Machine identifiers, confidence values, model reasoning, and edit verbs remain internal.

## HTML report

Keep the project/source summary, factual progression summary, narration dashboard, event/story information, selected-subtitle summary, processing cost, and an explanation that `[CUT]` means no detected voice. Do not enumerate every VAD gap or present editorial recommendations.

An AviUtl-shaped, horizontally scaled story timeline is deferred in `plans/future ideas/long-stream-editorial-analysis.md`.

## Pipeline boundary

Preserve source probing, transcription, visual learning, and reusable game knowledge. Semantic analysis now emits factual observations only; it does not draft edit recommendations or run a targeted visual adjudication pass. Global reconciliation performs factual story/event synthesis plus narration selection. Action planning performs subtitle selection and deterministic artifact generation. The current factual-prompt change invalidates from `semantic_spans`.

Remove the Terra transcript-cut nomination and visual-cut adjudication calls. Existing checkpoints retain historical data, but regenerated JSON, HTML, and EXO outputs omit obsolete editorial suggestions.

## Narration reference

The completed ConnorDawg source-to-edit study is distilled in
[`narration-practices.md`](narration-practices.md). Its viewing-contract, knowledge-gap, source-audio,
discovery-state, and cohesive-passage rules now guide factual synthesis and narration briefs without
introducing creator-specific detectors or requiring the reference files at runtime.
