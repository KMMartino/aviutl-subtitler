"""Event-oriented visual interpretation for long-form editorial projects."""

from __future__ import annotations

from .editorial_locale import output_language_instruction
from .media_analysis import OpenAIMediaAnalysisProvider


class OpenAIEditorialVisualProvider(OpenAIMediaAnalysisProvider):
    """Use shared sampling/transport without media-library B-roll semantics."""

    def _analysis_instruction(self, media_kind: str, title: str, max_ranges: int) -> str:
        return (
            "Task: analyze every labeled chronological sample as evidence for a long-form editorial event map. "
            "Identify meaningful changes in activity, location, objective, attempt, encounter, menu or upgrade "
            "state, interruption, failure/recovery, and visible payoff. Do not judge material as B-roll and do "
            "not label desktop or loading footage unusable merely because it is outside gameplay; describe what "
            "visibly happens so a later editor can interpret it with the transcript. Prefer event-scale ranges "
            "over broad content categories, while avoiding invented boundaries caused only by sampling. Repeated "
            "attempts may be separate when progress, strategy, outcome, or creator reaction visibly changes. "
            "Describe only what the sampled frames establish. Empty party slots, a dark transition, statistics, "
            "or a return to a title/menu do not by themselves establish victory or defeat. Treat the outcome as "
            "uncertain unless an explicit result screen establishes it; later transcript and continuity evidence "
            "will make the final outcome judgment. When a frame contains a progress, result, unlock, statistics, "
            "epilogue, game-over, or clear screen, transcribe the visible cue and describe the state change "
            "factually instead of collapsing it into a generic menu or ending label. Distinguish the observed cue "
            "from any inferred run outcome so downstream analysis can reconcile it with the full transcript. "
            "Set handoff_required only when the sampled stills do not adequately establish a decision-relevant "
            "state, transition, nonverbal event, or precise boundary and a later cut decision should inspect "
            "frames directly. Give the unresolved question in handoff_reason. Do not request handoff merely "
            "because the scene is visually busy or unfamiliar. "
            f"Use up to {max_ranges} chronological ranges and cover the sampled timeline. Completion means "
            "every sample index is covered exactly once, uncertainty is explicit, and the response matches the "
            "provided schema. "
            f"Media kind: {media_kind}. Filename title: {title}. "
            + (f"Project/game context: {self.editorial_context}. " if self.editorial_context else "")
            + output_language_instruction(self.output_locale)
            + " Each following image is labeled with its sample index and timestamp."
        )

    def _boundary_instruction(self) -> str:
        return (
            "Task: refine every supplied candidate event boundary in a long recording. Classify each ordered probe as left, right, "
            "or new according to the visible activity, objective, location, attempt, interruption, or outcome. "
            "Use a new scene when the probe establishes a distinct editorial event, not merely a different "
            "camera frame. Keep desktop, loading, failure, recovery, and menu activity factual rather than "
            "automatically calling it unusable. Treat victory or defeat as uncertain when the only evidence is "
            "HUD disappearance, party slots, a transition, statistics, or a menu return. Completion means one "
            "decision exists for every probe and the response matches the provided schema. "
            + output_language_instruction(self.output_locale)
        )
