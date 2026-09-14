"""Bind editorial checkpoints to the same immutable operation store as subtitles."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .api_usage import ApiUsageLedger
from .editorial_project import CHECKPOINT_STAGES, PROJECT_CHECKPOINT_STAGES
from .editorial_resume import invalidate_editorial_from
from .operation_store import ArtifactError, OperationStore, content_digest


def _reuse_inputs(stage: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Report images and admission limits do not change collected evidence."""
    if isinstance(inputs.get("source"), dict):
        inputs = {**inputs, "source": {k: v for k, v in inputs["source"].items() if k != "reference_frames"}}
    if stage != "transcription":
        return inputs
    parameters = inputs.get("parameters", {})
    cost = parameters.get("cost")
    if not isinstance(cost, dict):
        return inputs
    return {**inputs, "parameters": {**parameters, "cost": {
        key: value for key, value in cost.items()
        if key not in ("max_estimated_api_cost_usd", "allow_api_spend")
    }}}


class EditorialOperations:
    def __init__(self, checkpoint_path: Path, project: dict[str, Any], executor: Any) -> None:
        self.project = project
        self.executor = executor
        self.store = OperationStore(checkpoint_path.with_suffix(".operations"), project["project_id"], ApiUsageLedger(), restore_usage=False)

    def inputs(self, stage: str, source: dict[str, Any] | None) -> dict[str, Any]:
        project = self.project
        dependencies = {}
        if source is not None:
            predecessors = CHECKPOINT_STAGES[:CHECKPOINT_STAGES.index(stage)]
            dependencies.update({name: self._revision(source["stages"][name]) for name in predecessors})
            if stage == "semantic_spans":
                dependencies.update({item["source_id"]: self._revision(item["stages"]["semantic_spans"])
                                     for item in project["sources"] if item["order"] < source["order"]})
        else:
            dependencies.update({item["source_id"]: self._revision(item["stages"]["local_reconciliation"])
                                 for item in project["sources"]})
            dependencies.update({name: self._revision(project["editorial_map"][name])
                                 for name in PROJECT_CHECKPOINT_STAGES[:PROJECT_CHECKPOINT_STAGES.index(stage)]})
        parameters: dict[str, Any] = getattr(self.executor, "operation_parameters", lambda _stage: {})(stage)
        intent_fields: tuple[str, ...] = (() if stage in ("source_probe", "transcription", "local_reconciliation") else
                         ("title_or_game", "objective", "output_locale", "processing_locale", "must_keep_notes", "de_emphasize_notes"))
        if source is None:
            intent_fields += ("target_duration_min_ms", "target_duration_max_ms", "subtitle_mode")
        return {
            "source": {key: value for key, value in source.items() if key not in ("stages", "status", "result", "export_audio", "reference_frames")}
            if source is not None else None,
            "intent": {key: project.get(key) for key in intent_fields},
            "parameters": parameters, "dependencies": dependencies,
        }

    @staticmethod
    def _revision(checkpoint: dict[str, Any]) -> str:
        return checkpoint.get("operation_result", {}).get("revision_id") or content_digest(checkpoint.get("output"))

    def prepare(self) -> str | None:
        """Verify saved payloads, then invalidate the earliest changed input contract."""
        candidates = [(stage, source, source["stages"][stage])
                      for stage in CHECKPOINT_STAGES for source in self.project["sources"]]
        candidates += [(stage, None, self.project["editorial_map"][stage]) for stage in PROJECT_CHECKPOINT_STAGES]
        for stage, source, checkpoint in candidates:
            reference = checkpoint.get("operation_result")
            if reference is not None:
                saved = self.store.resolve(reference)
                if content_digest(saved) != content_digest(checkpoint.get("output")):
                    raise ArtifactError(f"The {stage} checkpoint differs from its immutable operation result")
            previous = checkpoint.get("operation_inputs")
            if previous is not None and _reuse_inputs(stage, previous) != _reuse_inputs(stage, self.inputs(stage, source)):
                invalidate_editorial_from(self.project, stage)
                return stage
        return None

    def execute(
        self, stage: str, source: dict[str, Any] | None, checkpoint: dict[str, Any],
        produce: Callable[[], Any], persist: Callable[[], None],
    ) -> dict[str, Any]:
        checkpoint.setdefault("operation_generation", uuid4().hex)
        current = self.inputs(stage, source)
        previous = checkpoint.get("operation_inputs")
        # Retain the original immutable key when recovering a published result
        # after a checkpoint-write failure. The producer still uses live limits.
        checkpoint["operation_inputs"] = (previous if previous is not None
            and _reuse_inputs(stage, previous) == _reuse_inputs(stage, current) else current)
        persist()
        inputs = {"generation": checkpoint["operation_generation"], **checkpoint["operation_inputs"]}
        scope = getattr(self.executor, "operation_scope", lambda _generation: nullcontext())

        def decode(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict):
                raise ValueError(f"{stage} returned no usable artifact")
            return value

        with scope(checkpoint["operation_generation"]):
            output = self.store.execute(stage, checkpoint["version"], inputs, produce, decode,
                                        output_files=lambda output: [Path(output[key]) for key in
                                            ({"transcription": ("transcript_path", "document_path"),
                                              "action_planning": ("adaptive_report_path", "adaptive_artifact_path")}
                                             .get(stage, ())) if output.get(key)],
                                        failure_payload=lambda exc: getattr(exc, "editorial_failure_output", None))
        checkpoint["operation_result"] = self.store.reference(stage, checkpoint["version"], inputs)
        return output
