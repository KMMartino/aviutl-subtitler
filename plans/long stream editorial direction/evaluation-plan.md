# Evaluation plan

The first evaluation set does not require a pre-authored machine-readable edit plan. Human review can establish useful metrics iteratively.

## Evaluation unit

Use a few hours of representative source material containing a mix of:

- uninterrupted gameplay worth keeping;
- silence during meaningful concentration or dramatic moments;
- repeated attempts with at least one milestone or payoff;
- routine traversal or repeated locations;
- menus/upgrades that are sometimes important and sometimes disposable;
- a topic or objective that disappears and later returns;
- at least one visually exciting but editorially repetitive passage;
- at least one visually quiet but narratively essential passage.

The FF16 example is a useful secondary reference for narration briefs and presentation modes, but should not be the only calibration source because it represents an unusually narration-heavy edit.

## Human review labels

For each proposed recommendation, the creator assigns:

- `accept` — recommendation and rationale are useful as written;
- `modify` — the underlying observation is useful but range, disposition, presentation, or rationale needs adjustment;
- `reject` — it would make the edit worse or is unsupported;
- `missed` — an important recommendation or connection was absent.

The reviewer can separately score boundary usefulness, thread connection, narration brief, and evidence accuracy. This prevents a good observation with a bad trim suggestion from being counted as wholly wrong.

## Initial metrics

- recommendation acceptance rate, with `modify` reported separately;
- severe-error rate: suggestions that remove an essential event, payoff, or meaningful silent moment;
- important-moment recall based on human-added `missed` items;
- thread-link precision for suggested distant connections;
- attempt-cluster accuracy: correct grouping, milestones, and outcome;
- boundary burden: median manual adjustment required from the soft handle range;
- narration-brief usefulness: whether it accurately jogs memory and provides enough facts, interpretation, and visual support;
- evidence traceability: whether the user can quickly verify why a recommendation exists;
- duration feasibility: whether accepted recommendations can plausibly reach the selected range;
- continuity damage: whether an accepted short plan accidentally removes buildup, spatial comprehension, conversational texture, or meaningful silence;
- selection efficiency: how much of the accepted short plan directly supports its premise, threads, or payoffs;
- blend quality: whether the chosen scenes receive an appropriate continuity-first or selection-first treatment instead of uniform compression;
- cost per source hour, broken down by speech, vision, and reasoning;
- resume integrity: no repeated completed work and identical stable IDs after interruption/relinking.

## Safety-weighted success criterion

False cuts are more damaging than missed cuts in version 1. Evaluation should weight `omit` and aggressive `condense` errors more heavily than conservative `review` recommendations.

A practical first milestone is not full automation. It is an HTML report in which the creator says that most accepted recommendations save search and memory effort, the most important narrative moments are present, and no silent but meaningful sequence is confidently mislabeled as disposable.

## Calibration loop

1. Run the system on a small source subset.
2. Have the creator label recommendations and add misses.
3. Convert repeated feedback into rubric examples and targeted regression fixtures.
4. Re-run on a held-out subset from the same project.
5. Test on a different game or an informational stream before generalizing thresholds.

Human labels should tune prompts, thresholds, and retrieval behavior. They should not become hardcoded detectors for FF16 or one creator's editing style.

## Avoiding example overfit

The evaluation set should deliberately contain paired cases that defeat simple lessons from the FF16 sample:

- a long, mostly silent encounter that should remain substantially intact;
- low-motion exploration whose atmosphere or geography matters;
- a routine-looking conversation containing the project's essential premise;
- visually spectacular footage that should be shortened;
- a lightly edited target where new narration would make the result worse;
- a heavily condensed target where relying only on subtractive trimming cannot meet the duration coherently.

Evaluate the same source against at least two target ranges. A good planner should change editorial philosophy as pressure rises while retaining the same understanding of facts, threads, and protected moments.
