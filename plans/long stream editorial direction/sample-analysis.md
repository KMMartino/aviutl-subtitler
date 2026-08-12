# FF16 sample analysis

## Scope and method

The reference project is `H:\3 Game\34 FF16\5 初期装備\sample.exo`. Its creative premise is a Final Fantasy XVI challenge run using only the weakest equipment.

The analysis used three forms of evidence:

1. The Shift-JIS EXO was parsed to reconstruct its timeline, source references, layer use, clip positions, and audio levels.
2. The app's hosted workflow produced a rough Japanese transcript of `3v.mp4`, the narration source actually referenced by the EXO. Generated output and sidecars are in `evidence/`.
3. The existing sampled-media analyzer inspected `6.mp4` and `7.mp4`, the gameplay recordings referenced by the finished sequence. It sampled 214 frames in total and identified coarse visual phases. The two visual calls cost approximately $0.32; the narration transcription cost approximately $0.23.

`1v.mp4` and `2v.mp4` were not transcribed because they are not referenced by `sample.exo` and therefore cannot be aligned to this example's edit decisions. They can be analyzed later as separate examples if useful.

## What the EXO actually contains

The EXO header declares a four-hour, 1920×1080, 60 fps project with 179 objects. That header does not describe four hours of finished content.

- The finished edited sequence runs from `00:00:00` to approximately `00:13:37.10`.
- A full, unedited copy of `7.mp4` appears later, from approximately `00:56:46.66` to `01:49:34.21`. It is workspace/reference material, not part of the completed 13:37 edit.
- The opening lasts about 14.6 seconds.
- `3v.mp4` is placed as an invisible video track plus an audible narration track. Its video scale is zero and its narration audio is amplified.
- The edited sequence uses 28 narration chunks totaling about 9 minutes 26 seconds.
- Gameplay from `6.mp4` and `7.mp4` is cut into many short, non-contiguous selections. Gameplay audio is normally muted beneath narration and restored for selected live moments.

The result is narration-led rather than merely narration-assisted: approximately 70% of the finished timeline is covered by post-recorded narration.

## Live gameplay anchors

The narration leaves seven meaningful live windows:

| Timeline | Approximate duration | Editorial role |
|---|---:|---|
| 01:24.85–01:28.30 | 3.45 s | Very short reaction or punch line |
| 01:51.48–02:21.40 | 29.92 s | Sustained encounter excerpt |
| 04:32.65–05:06.11 | 33.47 s | Demonstration/highlight |
| 07:40.95–08:46.36 | 65.42 s | Major encounter anchor |
| 08:52.55–09:58.38 | 65.83 s | Major encounter anchor |
| 10:02.25–10:20.45 | 18.20 s | Short live beat |
| 11:54.48–12:29.35 | 34.87 s | Outcome or payoff beat |

This distribution is useful evidence for soft handles. A meaningful preserved moment can be a few seconds or more than a minute; a fixed amount of pre-roll and post-roll would be wrong.

## Narration structure

The rough transcript is not just a description of what is visible. It repeatedly does one or more of the following:

- establishes the next objective and why it matters;
- identifies optional encounters and rewards that support the challenge build;
- compresses ordinary enemies, traversal, and long damage-sponge encounters;
- explains tactics while chosen footage demonstrates them;
- turns failed attempts or mistakes into jokes and character beats;
- summarizes repeated actions instead of replaying every occurrence;
- interprets the challenge's increasing damage and defense disadvantage;
- foreshadows later abilities and future difficulty;
- closes one progression unit and opens the next.

The narration therefore provides an editorial argument: *what changed, why the chosen moment matters to the challenge, and what the viewer should anticipate next*. It is not a replacement transcript for deleted footage.

The app transcript contains normal rough-ASR issues—duplicated takes, false starts, and likely term errors. Those imperfections are themselves informative: the analysis artifact should preserve source evidence and uncertainty, while narration suggestions should be concise semantic notes rather than pretending to be final script copy.

## Visual structure of the sources

The sampled visual pass found useful semantic phases in `6.mp4`:

- optional forest enemy, post-fight looting, and quiet idle time;
- dungeon entry and routine mine combat;
- a cannon-wielding boss with a failed attempt, reload, rematch, and victory;
- ability/level-up UI;
- further dungeon traversal and minor encounters;
- palace arrival and low-action door/transition beats;
- major crystal dragon boss phases and post-boss story footage.

It found the following in `7.mp4`:

- crystal-temple approach and save UI;
- boss introduction and small-enemy waves;
- tutorial overlays;
- a long, visually spectacular summon battle;
- black/loading transitions and status menus;
- grounded combat in a ruined hall;
- rewards, hub travel, equipment, and shop screens.

These are coarse machine observations, not authoritative editorial judgments. In particular, a static menu can be essential proof of the challenge constraint, while an effects-heavy battle can still be repetitive. Motion or visual spectacle cannot substitute for narrative context.

## What the example proves

### 1. Selection and presentation are separate decisions

A segment can be important enough to keep as evidence but not important enough to hear in real time. Conversely, a short live reaction can deserve original audio even when adjacent combat is summarized.

Each recommendation therefore needs two axes:

- **content disposition:** keep, condense, omit, connect, or review;
- **presentation mode:** live, live excerpt, narration over representative footage, montage under narration, or narration-only bridge.

### 2. Narration planning needs facts and intent, not generated prose alone

For each proposed narration block, the user needs:

- the factual events being compressed;
- why those events matter to the run or topic;
- useful interpretation, payoff, or foreshadowing;
- the source moments that support each claim;
- suggested representative visuals and live-audio anchors;
- uncertainties that the user should verify.

This jogs the creator's memory while leaving the final voice and editorial direction to them.

### 3. Repetition is semantic, not merely visual

The cannonier sequence includes attempts, reloads, tactical adaptation, and eventual victory. A system should group these as one attempt cluster, then identify milestones within it. Similar-looking frames alone cannot decide which try matters.

### 4. Low-motion footage is not automatically stale

Loot, ability menus, saves, and equipment comparisons are low-motion but directly explain the challenge. Silence and motion scores may help retrieve candidates; they must not determine cuts.

### 5. Chronology remains the backbone even in a narration-heavy edit

The example jumps aggressively within the source recordings, yet the finished story still advances through objectives, encounters, upgrades, and outcomes. Cross-links should normally preserve causal order unless the report explicitly recommends a retrospective, setup, or foreshadowing connection.

## What this example must not dictate

This is a highly condensed, narration-led project in which the voiceover existed as the edit's baseline. The proposed product assumes narration does not yet exist and defaults to lightly edited continuity. It must not infer that:

- 70% narration coverage is desirable for ordinary long-stream projects;
- all traversal or menus should be removed;
- only bosses deserve live presentation;
- high motion means keep and silence means cut;
- the user wants gameplay recut to a generated script.

Instead, this example adds a high-compression presentation mode to a broader system whose default is still `live` or `live excerpt`.

It is best understood as evidence for the selection-first end of the system: begin with the claims, encounters, demonstrations, and reactions that earn a place, then supply only enough source footage to support them. It is not representative evidence for the continuity-first end: preserve the lived progression of a session and remove only material whose absence improves it.

The product must support both. The FF16 example should help define selection-first narration briefs and live anchors, while future evaluation footage must provide equally strong evidence for lightly edited continuity, ambient pacing, conversation, exploration, and meaningful silent play.

## Product conclusion from the example

The central artifact should be an **editorial map**, not a cut list. It should describe a hierarchy of source observations, story/topic threads, attempt clusters, recommendations, narration briefs, and links between distant moments. Each meaningful span should preserve both its continuity-first case and its selection-first case so later duration planning does not have to reinterpret the source through only one editorial philosophy. The HTML view should let the user read this as an editorial argument; the EXO should expose the same evidence as non-destructive, overlapping marker layers.
