# Creator workspace workshop

Status: design proposal only. No phase 4 UI or phase 5 feature implementation is
included in this migration. These are options to discuss together later.

## Is phase 4 a frontend redesign?

Yes. Phase 4 substantially redesigns the frontend's navigation, information
organization, and task flow around projects, sources, and results. It goes beyond
visual styling. Existing source controls, settings, review tools, and editors can
be reused within that structure. The completed backend migration supplies reusable
processing and recovery; the frontend still needs project/result browsing and the
adapters that connect those views to the stored work. This remains a proposal for
our discussion, with implementation held until we agree on the direction.

## Recommended product shape

A project is a creator's working context: sources, transcripts, generated results,
review decisions and exports. It can contain multiple recordings. A workflow is a
named action available for those materials. The user sees their work and its next
useful actions; process graphs remain internal.

Suggested desktop structure:

- Home: recent projects, New project, and a quick one-off file/URL action.
- Project: a source list, a central result viewer, and a small activity/status area.
- Sources: local recordings or URLs, track selection, origin, and available results.
- Results: Transcript, Subtitles, Editing guide, and eventually Clips or Chapters.
- A result shows its source, creation time, current/stale status, and relevant actions
  such as Edit, Regenerate, Export, or Use in another task.
- Processing location and model details live in settings. The primary question is
  what the creator wants to make, rather than which engine to launch.

Example: import a livestream once, create its transcript, generate subtitles, then
create an editing guide from that transcript. Revising subtitle appearance should
immediately reuse text. Changing the cleanup instructions creates new subtitle
text. Editing source words creates a transcript successor and marks dependent
results as needing an update, while preserving the previous exports.

## UI alternatives

| Approach | Strength | Limitation | Recommendation |
|---|---|---|---|
| Project with Sources and Results | Makes reuse and multiple recordings visible; supports branching work | Needs a clear distinction between current and previous results | Primary workspace |
| One guided wizard per task | Fast for a first run | Hides existing reusable work; becomes awkward with revisions | Use within a task, not as the entire app |
| Task dashboard with recent outputs | Lowest initial navigation complexity | Weak ownership when several outputs use the same recording | Keep as Home/quick actions |

No node editor, connection drawing, or user-authored execution graph is proposed.

## Interaction details to settle

1. Project scope: one recording by default, or a broader series/session? Recommended:
   start with one recording and allow more sources later without changing projects.
2. Project storage: a user-visible folder or app-managed storage? Recommended:
   app-managed by default, with a selectable project location. Source files may stay
   where they are; copied/downloaded media must be clearly distinguished.
3. Result history: show every revision, or a current result with expandable history?
   Recommended: current result first; retain previous versions behind History.
4. Review flow: one consolidated review screen or a task-specific viewer?
   Recommended: task-specific review using shared Save, Apply, Cancel and resume
   behavior. Subtitle text, silence cuts and footage references need different tools.
5. Automation: automatically regenerate stale results or ask when opening them?
   Recommended: mark them stale and offer Update. Re-exporting unchanged text is
   cheap; new hosted analysis should show its estimated incremental cost.
6. Quick use: must every single subtitle export require naming a project?
   Recommended: no. Create a lightweight project automatically and let the user
   rename or discard it later.
7. External editing: how prominent should AviUtl round trips remain?
   Recommended: a first-class Import reviewed EXO action linked to its original
   export, alongside internal review where the app can do it well.

These are discussion prompts, not questions that block backend implementation.

## First UI slice after agreement

Build the project/source/result shell and connect the existing subtitle and editing
workflows to it. Show which transcript will be reused before starting a task. Add
clear interrupted/complete/stale states, reopen pending reviews, and preserve an
existing result while a successor is running. Do not redesign every detailed editor
at once. Acceptance example: one recording, one transcript, two outputs, close and
reopen, change subtitle appearance, export again without transcription.

The current migration supplies durable operation results and recovery. A project
index, unified result browsing, user-selected revision history, and a UI for choosing
an existing transcript still need this product design and implementation phase.

## Phase 5 ideas — intentionally not implemented

| Idea | Composition | Why it may be useful |
|---|---|---|
| Livestream highlights | Acquire → transcribe → optional visual evidence → select ranges → review → export clips | Directly addresses the original URL-to-clips request |
| Find an explanation or reaction | Existing transcript/evidence → select ranges with a user goal → review → export | Reuses the same range selector with different parameters |
| Chapters and show notes | Existing timed evidence → summarize/group → review → export timestamps/text | Useful without encoding another video |
| Several output formats | Approved ranges → layout/crop/subtitle parameters → export variants | Reuses selections; avoids paying for repeated analysis |
| Stream recap or narration outline | Existing factual guide → summarize evidence for the desired audience → review | Extends the current editing guide without pretending suggestions are a finished script |
| Extract useful quotes/tutorial moments | Existing transcript → select ranges under a topic and duration policy → review | Same selection operation, not another canonical workflow |
| Rework an existing edit | Import reviewed artifact → recover source ranges → update selected text or references → export | Builds on the AviUtl round trip already present |

Recommended first feature: a small batch of reviewable clip candidates from a saved
transcript, with the source preview and exact start/end times. Add visual analysis
when the requested moment cannot be identified from speech. The creator approves
ranges before full-resolution rendering. Measure whether the candidates are useful
and whether their boundaries play cleanly before expanding to many formats.

## Avoiding unnecessary processes

Use one range-selection operation parameterized by goal, duration, count and evidence
policy. Use one media export operation parameterized by accepted ranges, layout,
resolution and subtitles. Use the same transcription operation with local/hosted
provider settings. Keep a distinct operation when its artifact contract or review
boundary is different: selecting ranges is not exporting a video, and generating
subtitle text is not approving cuts. Sharing an LLM client alone does not make two
semantically different results the same operation.

## Future acceptance examples

- Re-running selection with a different target length reuses transcription.
- Editing one candidate does not regenerate the other candidates.
- Exporting vertical and landscape versions reuses approved source ranges.
- Failed rendering preserves approved clips and all paid analysis.
- Every displayed clip can identify its source, selection revision and timing units.
- Project-wide cost includes failed and superseded attempts, separately from the
  estimated cost of producing the current result from scratch.
