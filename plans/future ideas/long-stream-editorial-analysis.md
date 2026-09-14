# Future Ideas: Long-Stream Editorial Analysis

Status: exploratory only. These ideas are not committed implementation scope.

The common objective is to improve the evidence available before editorial decisions are made, or to introduce a genuinely different decision mechanism. They should not become additional layers of agents that repeatedly reconcile a weak initial plan.

## 1. Locally controlled multimodal inspection

Updated 2026-09-08 after reviewing preserved August experiments and discussing model capability with the user. This supersedes the earlier decision to wait for a better cheap video model.

Keep frame extraction local: ordered timestamps, configurable sampling, selective high-resolution crops, frame caching, and small transfers. Uploading an encoded video is not itself an improvement in understanding. A provider's video endpoint is optional only if a measured model/modality advantage justifies it; fixed-frame image input remains a valid primary route.

The useful improvement is answering a consequential question using adequate evidence: what changed, whether a result is established, what an item says, whether two attempts differ, or whether a quiet passage supplies necessary context. Existing dense passes and targeted reviews already attempted this. Additional calls must supply missing evidence or demonstrably improve interpretation, rather than repeat general summaries.

Use one reusable inspection operation parameterized by source range, question, modalities, sampling, and image detail. Separate observed facts, inferred intent, and unresolved outcomes. A model's self-reported confidence alone must not decide whether a claim gets verified.

Compare stronger models on identical evidence, then separately compare best-system configurations. Do not gate a bounded model trial on a hypothetical future release. Current model choice and price must be verified when implementing; historical frame-cost estimates are not current budgets.

The product goal remains better concrete edits. Evidence visibility, diagnostics, and creator feedback support development and quality measurement; they are not substitutes for generated selections and usable cut output. UI timelines and richer reports remain secondary to decision quality.

References:

- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/guides/images-vision
- https://ai.google.dev/gemini-api/docs/video-understanding

## 2. Separate gameplay-audio analysis

When paired recordings contain audio in both files, retain facecam audio as the speech source and analyze gameplay audio as an independent structural signal. Useful evidence includes:

- boss music and battle-state transitions;
- victory or defeat stingers;
- menu, loading, result-screen, and crash sounds;
- combat-intensity changes;
- repeated music associated with retries;
- transitions into cinematics or endings.

Start with inexpensive spectral novelty and audio embeddings. Semantic audio-event models such as YAMNet or CLAP are possible later additions.

References:

- https://www.tensorflow.org/hub/tutorials/yamnet
- https://github.com/laion-ai/CLAP

## 3. Optional local facecam reaction map

Use facecam video as an optional significance signal rather than only an audio source. Analyze locally for reaction intensity, head movement, concentration, sudden movement, laughter-like facial motion, absence from camera, and visible interruptions.

Do not claim authoritative emotional interpretation. Store observations such as `strong_visible_reaction`, not inferred private mental states. MediaPipe Face Landmarker can provide video landmarks, transformation matrices, and blendshape scores without uploading face images.

Reference:

- https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker

## 4. Visual-state memory

Generate embeddings for representative gameplay frames and maintain a compact searchable state index. Use it to recognize:

- returns to the same menu, shop, camp, or area;
- repeated travel and repeated attempts;
- another encounter with the same boss;
- before-and-after inventory or build states;
- recurring title, loading, crash, and recovery screens;
- related moments across multiple source files.

Distill clusters into concise facts before model use. Do not place raw embedding collections into prompts. Possible local foundations include DINOv2 and OpenCLIP.

References:

- https://github.com/facebookresearch/dinov2
- https://github.com/mlfoundations/open_clip

## 5. Targeted game-UI OCR and state tracking

Apply multilingual OCR only to frames already classified as important UI states, such as:

- result and statistics screens;
- difficulty and run-condition settings;
- boss and enemy names;
- party deaths and status changes;
- acquired equipment and learned skills;
- stage and floor identifiers;
- explicit victory, defeat, or completion text.

Convert recognized text into structured state changes with visual provenance. This is separate from chat-overlay OCR, which remains out of scope. PaddleOCR is one possible implementation basis.

Reference:

- https://github.com/PaddlePaddle/PaddleOCR

## 6. Audiovisual alignment of finished videos and source recordings

Extend the existing transcript alignment used by the reference study. Match silent gameplay and narration-covered footage using:

- audio fingerprints for retained game or live audio;
- visual embeddings or perceptual hashes for footage whose audio was replaced;
- sequence alignment for continuous retention and jump cuts;
- unmatched finished-video spans for narration, inserts, and cold opens.

This could measure actual editorial behavior: clip lengths, cut density, retry compression, montage construction, narration placement, displaced source footage, and changes in retention posture. Chromaprint is a possible audio-matching component.

Reference:

- https://github.com/acoustid/chromaprint

## 7. Personal editorial fingerprint

Compare a generated plan and EXO with the project the user ultimately edits. Record aggregate preferences such as:

- suggestions accepted or rejected;
- ranges shortened beyond the suggestion;
- preserves that were ultimately cut;
- narration and montage suggestions used;
- preferred live-clip duration;
- tolerance for menus, travel, retries, and mechanical explanation.

Begin with a compact preference profile and representative examples rather than custom model training. Keep it user-specific and bounded to avoid context growth.

## 8. Viewer-knowledge ledger

For every broad story beat, represent:

- what the viewer knows beforehand;
- the new fact or state learned;
- questions opened and answered;
- prerequisites for understanding the next beat;
- the evidence that communicates each fact.

Use narration only when an essential knowledge transition cannot be conveyed efficiently through retained source material. This offers a principled basis for narration instead of selecting it merely because a range is long or menu-heavy.

## 9. Constraint-based fine-cut optimizer

Separate semantic judgment from final timeline selection:

1. The broad director locks story obligations and editing posture.
2. Local analysis scores atomic candidates for novelty, personality, causality, tension, redundancy, and continuity.
3. A deterministic optimizer selects the best compatible chronological sequence.

Possible constraints include mandatory setup/payoff dependencies, speech-safe boundaries, minimum clip lengths, state continuity, representative coverage, and a soft duration range. This is a high-effort architectural experiment, but it could prevent locally attractive edits from damaging the global story.

## 10. Disposable proof cut

Compile a low-resolution preview containing proposed cuts, representative montage selections, and cards standing in for unrecorded narration. It is not a production edit and does not change the suggestion-only product contract.

The user or an evaluator supplied with locally sampled frames and relevant audio could evaluate the actual sequence rather than another textual plan. A video-upload API is not required, and model approval alone does not establish enjoyable pacing. Pursue this only after upstream evidence and planning quality are reliable.

## 11. Dedicated transition and motion analysis

Evaluate PySceneDetect, TransNet V2, compressed-video motion vectors, or optical-flow summaries as supporting signals for cutscenes, loading transitions, title/menu changes, crashes, and prerecorded non-game material.

This is lower priority because gameplay camera movement does not behave like cinematic shot boundaries, and the application already has frame differences and temporal bursts.

References:

- https://www.scenedetect.com/docs/latest/
- https://github.com/soCzech/TransNetV2

## 12. AviUtl-shaped horizontal story timeline

Present the factual event and story graph in the HTML report as a horizontal, time-scaled timeline that mirrors the AviUtl project layout. Keep the same lane ordering, category colors, source offsets, event spans, and setup/payoff thread identifiers used by the generated EXO so the browser view and editing timeline remain directly comparable.

Support multiple scales of evidence without turning the view into another recommendation system:

- long-running phases such as stages, areas, attempts, and boss battles;
- atomic events such as choices, acquisitions, failures, and state changes;
- story threads connecting setup events to later consequences or payoffs;
- overlapping events packed into separate horizontal lanes;
- zooming and horizontal scrolling for multi-hour projects.

This is a presentation feature only. The factual event graph remains the source of truth, and the timeline must not invent editorial actions.

## 13. Adaptive selected-subtitle styling

Extend the selected-subtitle pass beyond the current basic emotional color treatment. Let factual speech context choose from a bounded style vocabulary for emphasis, while keeping timing and wording cleanup independent from styling.

Potential inputs include delivery intensity, semantic importance, humor, surprise, calm explanation, uncertainty, and narrative payoff. Potential outputs include restrained changes to outline color, weight, scale, placement, or entrance treatment. Keep styles predictable, readable, and user-configurable; avoid free-form effects that cannot map cleanly to AviUtl objects.

## 14. Vocal performance beyond the transcript

Use raw VAD activity minus reliable aligned word coverage to nominate unexplained vocal activity. Merge candidates and add context before audio inspection. This is not a non-speech classifier: missing transcription, weak alignment, and noise also produce residuals. Laughter during speech or vocalizations missed by VAD require other candidate signals or sampling checks. Do not compare VAD with broad speech regions derived from the same detector.

A bounded audio inspection should distinguish observable laughter, gasps, cries, unintelligible speech, breathing, and non-vocal sounds, with source timestamps. Preserve onset and recovery when they contribute to a reaction. Do not infer entertainment value from loudness alone. Meaningful delivery within recognized speech is another reason for selective audio inspection.

Use the configured microphone/speech source; gameplay audio remains a separate signal. The visual/text decision model may not accept audio, so supply audio-derived evidence through an explicit adapter instead of pretending it listened. Detector misses and unexamined audio remain unknown rather than evidence of silence.

## 15. Adaptive cutting: proposed immediate scope

User narrowed the discussion on 2026-09-08 to restoring intelligence to cutting. This section records a proposed implementation design, not completed behavior or authorization for additional hosted expenditure.

Goal: tighten the existing chronological recording by keeping meaningful voice-free material and removing genuinely expendable spoken material. Produce concrete source-timed cuts. No target-runtime director, rearrangement, montage, invented narration, or creative effects are required. Existing narration tasks stay separate; adaptive cuts must not assume future narration will repair missing context.

### One decision process for speech and silence

- Retain deterministic voice-gap discovery as candidate generation, not an automatic keep/remove verdict.
- Inspect all source neighborhoods, using overlapping context, to find expendable complete spoken thoughts as well as quiet material. Cheap nomination must not permanently exclude possible cuts or important content from the strong judge.
- Group adjacent gap and utterance candidates into meaningful local passages. Batch judgments so a pause is assessed within its surrounding action and conversation, rather than in an isolated API request.
- Supply aligned speech, locally sampled gameplay frames, necessary readable crops, audio-performance findings when available, and compact factual context with retrievable source references. Reuse existing analysis when present; do not require rebuilding the whole canonical event/director pipeline to run this cutter.
- Ask what deleting the range would change in the viewer's understanding or experience, and what concrete improvement deletion provides. Inspect the proposed retained lead-in and continuation. Mere presence of a game event is not sufficient reason to veto a cut; absence of words is not sufficient reason to cut.
- Return keep, remove, or shorten with concrete subranges, supporting evidence, and any retained range required to replace a repeated explanation. Request focused inspection when a specific missing fact could change the decision. Bound retries; unresolved material remains intact without forcing a user review screen for every uncertainty.
- Protect complete thoughts and consequential discoveries, but allow removal of complete redundant thoughts, interruptions, repeated administration, and equivalent repeated procedure. Verify redundancy against actual retained evidence. No model must fill a runtime quota.

### Starting model and evidence policy

Start intentionally above the previous model tier: GPT-6 Astra with medium reasoning for substantive cutting judgments and consequential visual interpretation. It supports text/image input, not audio. Cheap models may handle mechanical extraction only when they cannot silently determine which evidence survives. Model settings should be parameters of the same operation so later down-tier evaluation changes no workflow semantics.

Use readable image detail where UI text matters and adaptive local sampling for transitions; do not send every frame at maximum detail. Selective audio inspection has its own provider contract. Model access and token-based cost estimates must be checked before running. Higher model selection is not authorization to exceed the user's existing paid-run cap.

### Latest scope and affordability clarification

Planning remains active; no product implementation yet. The user requires the existing deterministic voice-gap cut-marker and narration-suggestion system to remain usable and unmodified in behavior when adaptive cutting is not selected. New adaptive analysis should use separate versioned operations/results; it must not overwrite reviewed markers, prior narration suggestions, or force the baseline through new paid analysis. Shared low-level collection improvements may be reused only while preserving baseline behavior and artifact compatibility.

The all-Astra-medium cost is rejected as a production target. Compare Astra low and Sol medium before selecting the initial implementation model. The earlier Astra-medium design below remains a workload estimate, not the selected default.

At verified Standard short-context prices, Sol medium with the same full workload (1.85M input / 0.24M output including reasoning) is $12.20 before cache-write premiums, $14.05 with all input at write rates; allow approximately $15–$18 with modest contingency. With existing evidence reused (1.25M / 0.18M), it is $8.60–$9.85 before contingency; allow approximately $10–$13. Sol promotional pricing is currently documented through at least 2026-11-21.

Astra low has the same token rates as Astra medium. No measured output reduction is available. Sensitivity only: if low produces 0–50% fewer total billed output tokens, full-run base cost is $24.50–$30.50 and reuse cost $17–$21.50. Do not represent this as a measured or guaranteed low-effort discount. Keep the previous $30–$45 full-run budgeting envelope until output usage is measured. Lower effort alone does not solve the input cost; the full Astra input-only amount is $18.50 before cache-write premiums. All estimates exclude fresh transcription, hosted audio classification, unrelated editorial stages, and repeated tuning.

### Layering and cost planning (2026-09-08)

User caps the model at Astra medium. Planning only; no product implementation or paid run is authorized by this estimate.

Layers: (1) local extraction/alignment and candidate signals; (2) reusable factual passage records from original frames/transcript, with targeted readable crops and optional separately modeled audio; (3) adaptive keep/remove/shorten judgment across speech and silence; (4) deterministic boundary/dependency compilation; (5) targeted checks of the actual retained joins. Collection records should capture state changes and observable action, not full old event graphs or editorial verdicts. Use the same parameterized inspection operation for broad collection and focused questions. Fold freshly inspected facts into the decision request where practical; do not force duplicate model calls merely to materialize a layer.

For preserved 3.game, duration is 12,641.284 seconds (210.688 minutes). Aug28 candidate recorded 3,534 visual samples, 753,994 visual input tokens and 77,827 output tokens. Its transcript cut nomination plus visual review recorded 719,207 input and 49,524 output tokens across 34 requests. These are historical Luna/Terra measurements, not measured Astra token counts or quality guarantees.

Illustrative all-Astra-medium Standard-rate envelope (all inputs include text/images and context repetition; outputs include reasoning):

- Refreshed factual collection: 0.75M input / 0.08M output = $11.50.
- Adaptive cutting, about 43 five-minute core windows plus contextual overlap: 0.90M input / 0.13M output = $15.50. The output assumption is about 3,000 billed output/reasoning tokens per window, not a fixed property of medium reasoning.
- Targeted unresolved evidence and join checks: 0.20M input / 0.03M output = $3.50.
- Base total $30.50; at all-input cache-write rates $35.125. Allow roughly $30–$45 for one complete pass including new visual facts and modest contingency.
- Reusing verified existing collection and replacing the full collection line with 0.15M input / 0.02M output for focused correction gives $21.50 base, $24.625 at all-input cache-write rates; plan approximately $20–$30. Reuse is conditional on source/settings identity; old visual conclusions are not blindly trusted.

Rates verified from https://developers.openai.com/api/docs/pricing: Astra Standard short-context input $10/M, cache writes $12.50/M, cached reads $1/M, output $50/M. Keep each request below the 272K long-input pricing threshold; total tokens across requests do not trigger it. No cache-hit, Batch/Flex, or regional discounts assumed. Image tokenization differs by model/detail, so historical token counts only anchor the workload scale. Medium reasoning does not impose a token or dollar ceiling.

These estimates exclude new transcription, optional hosted audio classification, unrelated subtitle/narration stages, repeated tuning runs, and local compute costs. Existing transcription is intended for reuse. Historical transcription cost was $0.8391564; it is not a new transcription quote. Audio classification cost requires a provider and detected candidate minutes before it can be estimated; local audio candidate extraction incurs no API charge. The $30–$45 estimate exceeds the earlier $10 editorial authorization, so a full paid run would need a separately agreed cap. No paid requests were made to produce this estimate.

### Compilation and seam checks

Compile decisions deterministically into existing cut-marker and reviewed-EXO paths. Enforce source bounds, paired-media synchronization, protected requirements, and exact retained dependencies. If two cuts would remove both versions of a supposedly redundant explanation, reject that conflicting combination. Boundary placement uses aligned utterances and actual reaction/readability needs rather than blindly applying the current tiny speech handles.

Check affected joins and clustered cuts, since individually plausible deletions can jointly create an incoherent jump. Use the same inspection/judgment operation on changed context; this is not a new global director or an unrestricted critique loop. Existing retained narration ranges require an explicit conflict policy; proposed narration must not silently justify deleting live evidence.

### How this differs from the August experiment

The August 28 comparison already used transcript cut nomination, visual review, and higher-tier handoffs. Its more expensive candidate became substantially more conservative, without demonstrated viewer-quality improvement. Do not simply restore that cascade.

The proposed change is unified multimodal keep/remove judgment, candidate coverage of spoken and unspoken material, inspection of actual resulting joins, and deterministic dependency enforcement. A strong judge sees original evidence instead of only weak-model recommendations. Specific missing evidence triggers another inspection; generic concern that something might matter does not justify unlimited preservation or repeated debate.

Evaluate against the current silence-only output and preserved failures. Measure meaningful quiet moments rescued, useful spoken cuts found, damaging removals, bad joins, and creator correction time. A system that keeps everything must fail the usefulness criterion. Retain ordinary passages and another recording/game as holdouts; lower the model tier only after it preserves these outcomes at lower cost.

## Suggested experiments and priorities

1. Adaptive cutting as above: compare current silence-only behavior with Astra-based direct judgments using the same source media and actual resulting joins. Begin with a bounded representative subset, then test whole-recording interactions.
2. Test unexplained vocal activity and gameplay-audio cues against known missed reactions and state transitions.
3. Use targeted readable UI evidence to correct consequential factual errors; inspect a sample of high-confidence findings too.
4. Explore visual-state retrieval for proving redundancy or meaningful differences across repeated menus, routes, and attempts. Similar appearance alone does not prove semantic redundancy.
5. For model comparisons, separate existing/stronger model from existing/richer evidence. Prefer actual output quality and correction burden over action counts, schema validity, or self-audit praise.

The viewer-knowledge ledger is useful now in a lightweight form: identify what a removal would erase that the next retained passage requires. The full optimizer, personal preference learning, and source-to-finished audiovisual alignment remain later work. The proposed proof cut is a way to evaluate actual output, not a replacement for producing useful editing artifacts. Presentation-only timelines and subtitle effects are lower priority.

Related historical review: ../editorial-capability-assessment-2026-09-08.html. Its earlier video-upload emphasis is superseded by section 1 here. The reconciled direction was agreed in chat; broad reimplementation remains deferred while adaptive cutting is discussed.
