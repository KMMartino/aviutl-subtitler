# Hosted Model Evaluation

Use this process when a new hosted model is credible for Japanese transcription, subtitle cleanup, or both. The goal is to improve the product's measured quality per dollar without letting a change in one stage hide a regression in the other.

## Current baselines

These are the best demonstrated defaults as of 2026-09-03. They are project-specific decisions based on the preserved subtitle workload, not general model rankings. Update this table only after a completed evaluation records the supporting evidence.

| Profile | Transcription | Cleanup | Notes |
|---|---|---|---|
| Overall price/performance | Gemini `gemini-3.8-flash`, low thinking | OpenAI `gpt-5.6-luna`, low reasoning | Best demonstrated production-acceptable value profile. Gemini transcription uses fine VAD chunks capped at 30 seconds. |
| OpenAI-only | OpenAI `gpt-transcribe` | OpenAI `gpt-5.6-luna`, low reasoning | Best demonstrated OpenAI-only profile and control for new evaluations. |
| Gemini-only price/performance | Gemini `gemini-3.8-flash`, low thinking | Gemini `gemini-3.6-flash`, minimal thinking | Best demonstrated all-Gemini value profile. |
| Gemini cleanup quality tier | Gemini `gemini-3.7-flash`, low thinking | Gemini `gemini-3.7-flash`, low thinking | Use when duplicate-fragment repair is worth more latency or cost than the 3.6 cleanup profile. |

The application catalog and configuration remain the source of truth for currently supported model IDs, tuning values, and prices. If this table disagrees with shipped behavior, investigate the evaluation evidence and correct the stale source rather than silently choosing one.

Latest completed release evaluation: [Gemini 3.8 Flash, 2026-09-03](model-evaluations/2026-09-03-gemini-3.8-flash.md). It displaced the overall and Gemini transcription defaults at low thinking; its cleanup result did not displace a baseline.

## Comparison design

Evaluate transcription and cleanup as two independent stages. Start every model release evaluation from one unchanged control and change exactly one stage per arm:

| Arm | Transcription | Cleanup | Purpose |
|---|---|---|---|
| Control | Current overall transcription winner | Current overall cleanup winner | Shared reference for both stages. |
| Transcription candidate | Candidate model | Current overall cleanup winner | Tests the candidate as a transcriber. Score the raw transcript before cleanup as the primary result. |
| Cleanup candidate | Current overall transcription winner | Candidate model | Tests the candidate as a cleaner using the same preserved pre-cleanup inputs as the control. |

For example, evaluating Gemini 3.8 Flash means comparing these three arms:

1. `gpt-transcribe` + `gpt-5.6-luna`/low control.
2. `gemini-3.8-flash` + `gpt-5.6-luna`/low transcription arm.
3. `gpt-transcribe` + `gemini-3.8-flash`/low cleanup arm, followed by an adjacent thinking level only if the evidence justifies it.

Do not use candidate-transcribed text as the cleanup candidate's input. That would confound the stages. After selecting each stage independently, an optional end-to-end run may confirm that the winning pair composes cleanly.

## Reasoning-level policy

Treat model identity and reasoning level as one evaluated profile. For a new model in an existing family, begin with the winning level from the nearest evaluated family member. Keep that level fixed for the first comparison so the model-version change is isolated.

Test an adjacent higher or lower level only when there is a concrete reason to expect a better quality/cost tradeoff, such as changed provider guidance, a material latency or token difference, format failures, missed corrections, harmful edits, or evidence that the task needs more or less reasoning. State the hypothesis before the additional paid run and compare it on the same preserved inputs. Do not exhaustively sweep every level by default.

If the provider changes the meaning or availability of its reasoning controls, treat the levels as new profiles rather than equivalent labels. Record the chosen level in the baseline table and every evaluation artifact. A model replaces a default only as a specific model-and-level profile.

For Gemini 3.8 Flash, the inherited starting point is low thinking from Gemini 3.7 Flash. Move to minimal or medium only if official guidance or the first fixed-level results provide grounds for that comparison.

## Procedure

1. Confirm the exact API model ID, availability, supported audio and structured-output features, thinking controls, deprecation status, and current official prices. Verify the model with the existing read-only model-list path before benchmarking.
2. State the fixed corpus, control, candidate arms, inherited reasoning level, any hypothesis-backed adjacent levels, metrics, estimated maximum spend, and API concurrency. Obtain explicit user authorization before any paid call.
3. Preserve the corpus, configuration, prompts, raw provider responses, raw transcripts, pre-cleanup subtitle inputs, final outputs, usage ledger, timing, and a manifest containing model IDs and run parameters in a dated evaluation directory. Never overwrite an earlier run.
4. Run the control and candidate arms against identical inputs. Keep established mechanics fixed unless the model cannot operate under them; record any necessary exception. Gemini transcription uses fine VAD chunks capped at 30 seconds unless a separate segment-length experiment earns a change.
5. Score transcription before cleanup against a human-reviewed canonical transcript and the source audio. First apply a production-acceptability gate for genuine omissions, hallucinations, incorrect words or numbers, and unusable handling of untranscribable speech. Faithfully captured stutters, repetitions, false starts, and harmless English/Japanese title-script choices are not defects merely because the canonical transcript normalizes them. When text and canonical disagree about a disfluency, the audio decides. Inspect representative and known-difficult samples; aggregate counts and text-only diffs are insufficient.
6. Score cleanup from identical preserved inputs for semantic preservation, valid corrections, harmful substitutions or deletions, duplicate-fragment repair, subtitle boundary decisions, schema/format compliance, retries, latency, tokens, and cost. A cleanup result that changes meaning fails regardless of speed or price.
7. Classify each arm against the control as pass or fail at the current production bar before comparing economics. Once multiple profiles pass, prefer the lowest total stage cost, using latency and operational reliability as tie-breakers. Do not pay more for tiny stylistic differences that are not production defects. Record a materially stronger but less economical result as a separate quality tier rather than replacing the value default.
8. If promoted, update every affected source together: approved backend and frontend catalogs, tuning, pricing, cost tests, defaults and fallback pairing, persistence migrations, verification, labels and both languages, documentation, and regression tests. A fallback must be a genuinely distinct available model.
9. Run the full backend and frontend verification suites from `AGENTS.md`. Propose the deterministic rebuild for user testing; rebuild only after approval and push only after the user approves the installed build.

The evaluation is complete when both stages have independent evidence, all paid cost is reported, every retained or changed default has a stated reason, and any implementation passes its applicable verification and product-testing gates.

Long-stream native-video evaluation is a separate experiment. Follow `plans/future ideas/long-stream-editorial-analysis.md`; do not fold it into the transcription/cleanup comparison.
