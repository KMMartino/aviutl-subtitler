# Future Ideas: Long-Stream Editorial Analysis

Status: exploratory only. These ideas are not committed implementation scope.

The common objective is to improve the evidence available before editorial decisions are made, or to introduce a genuinely different decision mechanism. They should not become additional layers of agents that repeatedly reconcile a weak initial plan.

## 1. Native-video broad analysis

Create a low-bitrate synchronized proxy with gameplay, optional facecam, microphone audio, and game audio. Send it to a native-video model only for broad interpretation:

- content and run type;
- opening contract;
- major acts and turning points;
- retries and recurring developments;
- supported outcome;
- broad retention posture by phase.

It should not choose precise cuts.

### Model assessment and hold decision

Do not treat the number of frames currently sent to Luna as an inherent advantage of a hosted video model. Frame extraction is locally controlled and can be increased to match a provider's effective sampling rate while retaining adaptive transition bursts, exact timestamps, selective high-resolution crops, transcript context, and much smaller transfers than the source video. Compare models at equal visual evidence as well as in each system's best configuration.

- **Luna remains the production baseline.** Its API accepts ordered images rather than video or audio, but the local pipeline can supply the same sampled observations as a video provider and can spend additional frames only where they are useful. At the current estimator, uniform one-frame-per-second analysis of a one-hour source would cost roughly $0.21 including the assumed per-frame output; input alone is roughly $0.06. A compact timeline response could reduce the output-dominated total.
- **Gemini Flash is the only current replacement candidate likely to offer a material quality benefit at equal frame coverage.** Its File API samples video at one frame per second and jointly processes audio, timestamps, and the visual sequence. Its potential advantage is learned long-video, temporal, narrative, and audiovisual reasoning—not access to frames that Luna could not receive. Gemini 3.7 Flash has encouraging published long-video results, but its expected quality/cost improvement over a tuned Luna pipeline is not yet sufficient to justify integration.
- **Qwen is primarily a token-efficiency candidate.** Hosted video preprocessing defaults to two frames per second but is capped at 2,000 frames, or about 0.56 fps across a one-hour source. Luna can receive the same frames or a better adaptive selection. Qwen may encode a dense temporal sequence more cheaply, but there is insufficient evidence that its temporal reasoning would consistently beat Luna on this editorial workload; its standard visual models also omit joint audio understanding.

Decision: wait for Google to release a materially better Flash-class model before implementing or paying for a comparison loop. Revisit when a new model offers stronger long-video evaluation, improved temporal resolution or sampling control, and a credible quality gain over Luna after accounting for Luna's adjustable frame budget. The future evaluation should include both an equal-evidence test and a best-system test. For hosted models, upload a locally generated low-bitrate synchronized proxy rather than assuming the original full-quality recording is required.

Candidate providers retained for a future bounded comparison are Gemini video understanding, Qwen video understanding, and the existing Luna broad-analysis pipeline. TwelveLabs remains relevant as a specialized structured-video baseline rather than a direct general-model replacement.

References:

- https://ai.google.dev/gemini-api/docs/video-understanding
- https://deepmind.google/models/gemini/flash/
- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://www.alibabacloud.com/help/en/model-studio/vision-model
- https://docs.twelvelabs.io/docs/guides/analyze-videos
- https://docs.twelvelabs.io/docs/guides/segment-videos

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

The user or a native-video critic could evaluate actual pacing and audiovisual continuity rather than reviewing another textual plan. Pursue this only after upstream evidence and planning quality are reliable.

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

## Suggested experiments

The most useful bounded experiments are:

1. Analyze the existing gameplay audio track separately and compare detected structural transitions with the current event timeline.
2. After Google releases a materially stronger Flash-class video model, run equal-evidence and best-system comparisons across Gemini, Qwen, and the current Luna broad-analysis pipeline; optionally retain TwelveLabs as a specialized segmentation baseline. Compare act structure, temporal causality, audiovisual alignment, continuity, outcome understanding, exact evidence recovery, transfer size, token usage, and hosted cost.
3. Embed representative frames from one recording and test whether repeated locations, menus, retries, and before/after states cluster reliably.

The longer-term high-upside work is audiovisual source-to-finished alignment followed by learning from the user's completed edits.
