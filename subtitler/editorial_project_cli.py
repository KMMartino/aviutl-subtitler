"""CLI entry point for creating and resuming long-form editorial projects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, cast

from .api_usage import ApiUsageLedger
from .audio import get_media_duration
from .config import default_config_path, load_workflow_config, project_root
from .editorial_locale import editorial_locale
from .editorial_hosted import HostedEditorialExecutorOptions, HostedEditorialStageExecutor
from .editorial_project import (
    EditorialProjectOptions,
    EditorialSourceInput,
    create_editorial_project,
    extend_editorial_project,
    load_editorial_checkpoint,
    relink_editorial_source,
    unresolved_editorial_sources,
    write_editorial_checkpoint,
)
from .editorial_report import write_editorial_html
from .editorial_review import apply_reviewed_editorial_cuts
from .editorial_resume import (
    inspect_editorial_resume,
    invalidate_editorial_from,
    relink_matching_editorial_prefix,
)
from .editorial_runner import EditorialRunInterrupted, run_editorial_project
from .errors import SubtitlerError
from .transcript_document import load_transcript_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or resume a suggestion-only editorial map")
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="Create a fingerprinted editorial project")
    initialize.add_argument("--checkpoint", required=True)
    initialize.add_argument("--source", action="append", default=[])
    initialize.add_argument("--source-spec", action="append", default=[])
    initialize.add_argument("--title", default="Recording")
    initialize.add_argument("--objective", default="Silence markers")
    initialize.add_argument("--target-min-sec", type=float, default=60)
    initialize.add_argument("--target-max-sec", type=float, default=60)
    initialize.add_argument("--must-keep", action="append", default=[])
    initialize.add_argument("--de-emphasize", action="append", default=[])
    initialize.add_argument("--subtitle-mode", choices=("full", "emphasis"), default="full")
    initialize.add_argument("--output-locale", choices=("en", "ja"), default="en")
    initialize.add_argument("--processing-locale", choices=("en", "ja"))
    initialize.add_argument("--config")
    initialize.add_argument("--analysis", action="store_true")

    start = commands.add_parser("start", help="Create a project and generate local silence markers")
    start.add_argument("--checkpoint", required=True)
    start.add_argument("--source", action="append", default=[])
    start.add_argument("--source-spec", action="append", default=[])
    start.add_argument("--title", default="Recording")
    start.add_argument("--objective", default="Silence markers")
    start.add_argument("--target-min-sec", type=float, default=60)
    start.add_argument("--target-max-sec", type=float, default=60)
    start.add_argument("--must-keep", action="append", default=[])
    start.add_argument("--de-emphasize", action="append", default=[])
    start.add_argument("--subtitle-mode", choices=("full", "emphasis"), default="full")
    start.add_argument("--output-locale", choices=("en", "ja"), default="en")
    start.add_argument("--processing-locale", choices=("en", "ja"))
    _add_run_arguments(start, include_checkpoint=False)

    run = commands.add_parser("run", help="Resume local silence detection from a checkpoint")
    _add_run_arguments(run, include_checkpoint=True)

    status = commands.add_parser("status", help="Print resumable project status as JSON")
    status.add_argument("--checkpoint", required=True)

    inspect = commands.add_parser("inspect", help="Inspect checkpoint compatibility and reuse choices")
    inspect.add_argument("--checkpoint", required=True)
    inspect.add_argument("--source-spec", action="append", default=[])

    relink = commands.add_parser("relink", help="Relink one moved source after fingerprint verification")
    relink.add_argument("--checkpoint", required=True)
    relink.add_argument("--source-id", required=True)
    relink.add_argument("--source", required=True)
    relink.add_argument("--role", choices=("audio", "visual"), default="visual")

    apply_cuts = commands.add_parser(
        "apply-cuts", help="Apply exact [CUT] markers from a reviewed EXO without re-encoding"
    )
    apply_cuts.add_argument("--review-project", required=True)
    apply_cuts.add_argument("--checkpoint")
    apply_cuts.add_argument("--output")
    apply_cuts.add_argument("--config", default=str(default_config_path("hosted-long-stream")))
    apply_cuts.add_argument("--env-file", default=str(project_root() / ".env"))
    apply_cuts.add_argument("--workspace")
    narrate = commands.add_parser("narrate", help="Add narration to completed guides without rerunning analysis")
    narrate.add_argument("--checkpoint", required=True)
    narrate.add_argument("--output-checkpoint", required=True)
    narrate.add_argument("--env-file", required=True)
    narrate.add_argument("--workspace", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "narrate":
            from .editorial_narration import apply_narration, generate_narration
            from .editorial_exo import write_editorial_exo_parts
            from .editorial_runner import _record_stage_cost
            from .env import load_env_file
            source = Path(args.checkpoint).resolve()
            destination = Path(args.output_checkpoint).resolve()
            if destination == source:
                raise SubtitlerError("Use a new checkpoint path to preserve the original guide")
            project = load_editorial_checkpoint(source)
            if project['editorial_map']['action_planning']['status'] != 'complete':
                raise SubtitlerError("Narration requires completed editing recommendations")
            import shutil
            if source.with_suffix('.operations').is_dir():
                shutil.copytree(source.with_suffix('.operations'), destination.with_suffix('.operations'), dirs_exist_ok=True)
            load_env_file(Path(args.env_file))
            try:
                narration = generate_narration(project, Path(args.workspace), {})
            except Exception as exc:
                _record_stage_cost(project, 'project', 'narration_suggestions', getattr(exc, 'editorial_failure_output', {}))
                write_editorial_checkpoint(destination, project)
                raise
            apply_narration(project, narration)
            _record_stage_cost(project, 'project', 'narration_suggestions', narration)
            parts = write_editorial_exo_parts(destination.with_suffix('.exo'), project)
            project['outputs'] = {'exo_path': parts[0]['path'], 'exo_parts': parts, 'html_path': str(destination.with_suffix('.html'))}
            write_editorial_checkpoint(destination, project)
            write_editorial_html(destination.with_suffix('.html'), project)
            print(f"Narration complete: {len(narration['narration_briefs'])} suggestions; ${narration['api_cost_usd']:.4f}")
            return 0
        if args.command == "init":
            return _initialize(args)
        if args.command == "run":
            return _run(args)
        if args.command == "start":
            initialization = _initialize(args)
            return _run(args) if initialization == 0 else initialization
        if args.command == "status":
            return _status(args)
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "relink":
            return _relink(args)
        if args.command == "apply-cuts":
            return _apply_cuts(args)
        raise SubtitlerError(f"Unknown editorial command: {args.command}")
    except EditorialRunInterrupted as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except SubtitlerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _initialize(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint).resolve()
    sources: list[EditorialSourceInput] = []
    for raw_path in args.source:
        path = Path(raw_path).resolve()
        duration_sec = get_media_duration(path)
        sources.append(EditorialSourceInput(path, round(duration_sec * 1000)))
    for raw_spec in args.source_spec:
        sources.append(_parse_source_spec(raw_spec))
    if not sources:
        raise SubtitlerError("Editorial analysis requires at least one --source or --source-spec")
    project = create_editorial_project(
        sources,
        EditorialProjectOptions(
            title_or_game=args.title.strip() or sources[0].path.stem,
            objective=args.objective.strip() or "Silence markers",
            target_duration_min_ms=round(args.target_min_sec * 1000),
            target_duration_max_ms=round(args.target_max_sec * 1000),
            must_keep_notes=tuple(args.must_keep),
            de_emphasize_notes=tuple(args.de_emphasize),
            subtitle_mode=args.subtitle_mode,
            output_locale=args.output_locale,
            processing_locale=editorial_locale(getattr(args, "processing_locale", None) or load_workflow_config(
                "hosted-long-stream", Path(args.config) if getattr(args, "config", None) else None,
            )["backend"]["language"]),
        ),
    )
    write_editorial_checkpoint(checkpoint, project)
    report = checkpoint.with_suffix(".html")
    if getattr(args, "analysis", False):
        write_editorial_html(report, project)
    print(f"Editorial checkpoint: {checkpoint}")
    if getattr(args, "analysis", False):
        print(f"Editorial report: {report}")
    return 0


def _run(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint).resolve()
    if getattr(args, "extend_project_spec", None):
        _extend_checkpoint(checkpoint, args.extend_project_spec)
    workspace = Path(args.workspace).resolve() if args.workspace else checkpoint.parent / f"{checkpoint.stem}.files"
    if not getattr(args, 'analysis', False):
        from .silence_markers import run_silence_markers
        config = load_workflow_config('hosted-long-stream', Path(args.config))
        run_silence_markers(checkpoint, workspace, audio_track=args.audio_track,
            game_audio_track=config.get('editorial', {}).get('game_audio_track'),
            exo=Path(args.exo).resolve() if getattr(args, 'exo', None) else None,
            source_specs=_decode_source_specs(getattr(args, 'source_spec', [])) or None)
        return 0
    report = Path(args.report).resolve() if args.report else checkpoint.with_suffix(".html")
    executor = HostedEditorialStageExecutor(
        HostedEditorialExecutorOptions(
            config_path=Path(args.config).resolve(),
            env_file=Path(args.env_file).resolve(),
            workspace=workspace,
            audio_track=args.audio_track,
            glossary_path=Path(args.glossary).resolve() if args.glossary else None,
            game_knowledge_path=(
                Path(args.game_knowledge_store).resolve()
                if args.game_knowledge_store
                else None
            ),
            transcript_artifacts=tuple(Path(item).resolve() for item in getattr(args, "transcript_artifact", [])),
        )
    )
    if executor.transcript_artifacts:
        project = load_editorial_checkpoint(checkpoint)
        source_keys = {(Path(source["visual_path"] if source.get("speech_source") == "gameplay" else source["audio_path"]).resolve(), 0 if source.get("media_mode") == "paired" else args.audio_track) for source in project["sources"]}
        if not executor.transcript_artifacts.keys() <= source_keys:
            raise SubtitlerError("A supplied transcript does not match this project's source files and audio track")
        if getattr(args, "restart_from", None) not in {"source_probe", "transcription"}:
            for source in project["sources"]:
                artifact = executor.transcript_artifacts.get((Path(source["visual_path"] if source.get("speech_source") == "gameplay" else source["audio_path"]).resolve(), 0 if source.get("media_mode") == "paired" else args.audio_track))
                stored = source["stages"]["transcription"]
                if artifact is not None and stored["status"] == "complete":
                    previous = load_transcript_document(Path(stored["output"].get("document_path") or stored["output"]["transcript_path"]))
                    if previous.revision_id != load_transcript_document(artifact).revision_id:
                        raise SubtitlerError("A supplied transcript changed; use --restart-from transcription to replace its downstream results")
    run_editorial_project(
        checkpoint,
        executor,
        report_path=report,
        exo_path=Path(args.exo).resolve() if getattr(args, "exo", None) else None,
        restart_from=getattr(args, "restart_from", None),
        source_specs=_decode_source_specs(getattr(args, "source_spec", [])) or None,
    )
    print(f"Editorial analysis complete: {report}")
    print(f"Editorial AviUtl project: {getattr(args, 'exo', None) or checkpoint.with_suffix('.exo')}")
    return 0


def _status(args: argparse.Namespace) -> int:
    project = load_editorial_checkpoint(Path(args.checkpoint).resolve())
    payload = {
        "project_id": project["project_id"],
        "status": project["editorial_map"]["status"],
        "pipeline_versions": project["pipeline_versions"],
        "sources": [
            {
                "source_id": source["source_id"],
                "order": source["order"],
                "name": source["original_name"],
                "media_mode": source["media_mode"],
                "audio_name": source["audio_original_name"],
                "visual_name": source["visual_original_name"],
                "status": source["status"],
                "stages": {
                    stage: checkpoint["status"]
                    for stage, checkpoint in source["stages"].items()
                },
            }
            for source in project["sources"]
        ],
        "unresolved_sources": unresolved_editorial_sources(project),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _inspect(args: argparse.Namespace) -> int:
    project = load_editorial_checkpoint(Path(args.checkpoint).resolve())
    source_specs = _decode_source_specs(args.source_spec) or None
    print(json.dumps(inspect_editorial_resume(project, source_specs), ensure_ascii=False))
    return 0


def _relink(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint).resolve()
    project = load_editorial_checkpoint(checkpoint)
    relink_editorial_source(project, args.source_id, Path(args.source), role=args.role)
    write_editorial_checkpoint(checkpoint, project)
    write_editorial_html(checkpoint.with_suffix(".html"), project)
    print(f"Relinked editorial source: {args.source_id}/{args.role}")
    return 0


def _apply_cuts(args: argparse.Namespace) -> int:
    review_project = Path(args.review_project).resolve()
    workspace = (
        Path(args.workspace).resolve()
        if args.workspace
        else review_project.parent / f"{review_project.stem}.files" / "narration-review"
    )
    usage = ApiUsageLedger()
    executor = HostedEditorialStageExecutor(
        HostedEditorialExecutorOptions(
            config_path=Path(args.config).resolve(),
            env_file=Path(args.env_file).resolve(),
            workspace=workspace,
        )
    )
    provider = executor.build_narration_review_provider(
        usage, workspace / "narration-review"
    )
    try:
        result = apply_reviewed_editorial_cuts(
            review_project,
            checkpoint_path=Path(args.checkpoint) if args.checkpoint else None,
            output_path=Path(args.output) if args.output else None,
            narration_provider=provider,
            provider_parameters=executor.operation_parameters("semantic_spans"),
            api_usage=usage,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
    finally:
        if provider is not None and hasattr(provider, "close"):
            provider.close()
    result["api_cost_usd"] = usage.total_cost_usd
    result["api_usage"] = [row.__dict__ for row in usage.rows]
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _parse_source_spec(raw_spec: str) -> EditorialSourceInput:
    try:
        spec = json.loads(raw_spec)
    except json.JSONDecodeError as exc:
        raise SubtitlerError(f"Editorial source specification is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise SubtitlerError("Editorial source specification must be a JSON object")
    mode = spec.get("mode")
    if mode not in {"single", "paired"}:
        raise SubtitlerError("Editorial source specification has an invalid mode")
    audio_value = spec.get("audioPath")
    visual_value = spec.get("visualPath")
    if not isinstance(audio_value, str) or not audio_value or not isinstance(visual_value, str) or not visual_value:
        raise SubtitlerError("Editorial source specification requires audioPath and visualPath")
    audio_path = Path(audio_value).resolve()
    visual_path = Path(visual_value).resolve()
    audio_duration_ms = round(get_media_duration(audio_path) * 1000)
    visual_duration_ms = round(get_media_duration(visual_path) * 1000)
    frame_rate_value = spec.get("frameRate")
    frame_rate = float(frame_rate_value) if isinstance(frame_rate_value, (int, float)) and not isinstance(frame_rate_value, bool) else None
    def optional_dimension(key: str) -> int | None:
        value = spec.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None
    pairing_basis = spec.get("pairingBasis")
    if pairing_basis not in {"single", "filename", "resolution", "manual"}:
        raise SubtitlerError("Editorial source specification has an invalid pairingBasis")
    if spec.get("roleConfirmed") is not True:
        raise SubtitlerError("Editorial source roles must be confirmed before analysis")
    speech_source = spec.get("speechSource", "facecam")
    if speech_source not in {"facecam", "gameplay"}:
        raise SubtitlerError("Invalid speech audio source")
    return EditorialSourceInput(
        speech_source=cast(Literal["facecam", "gameplay"], speech_source),
        path=visual_path,
        duration_ms=visual_duration_ms,
        audio_path=audio_path,
        visual_path=visual_path,
        audio_duration_ms=audio_duration_ms,
        visual_duration_ms=visual_duration_ms,
        frame_rate=frame_rate,
        width=optional_dimension("width"),
        height=optional_dimension("height"),
        audio_width=optional_dimension("audioWidth"),
        audio_height=optional_dimension("audioHeight"),
        media_mode=cast(Literal["single", "paired"], mode),
        pairing_basis=cast(Literal["single", "filename", "resolution", "manual"], pairing_basis),
    )


def _add_run_arguments(parser: argparse.ArgumentParser, *, include_checkpoint: bool) -> None:
    if include_checkpoint:
        parser.add_argument("--checkpoint", required=True)
        parser.add_argument("--source-spec", action="append", default=[])
        parser.add_argument("--extend-project-spec")
        parser.add_argument(
            "--restart-from",
            choices=(
                "compatible",
                "source_probe",
                "transcription",
                "visual_learning",
                "semantic_spans",
                "local_reconciliation",
                "global_reconciliation",
                "action_planning",
                "editorial_assets",
            ),
            default="compatible",
        )
    parser.add_argument("--analysis", action="store_true", help="Run the shelved hosted editorial pipeline instead of local silence markers")
    parser.add_argument("--config", default=str(default_config_path("hosted-long-stream")))
    parser.add_argument("--env-file", default=str(project_root() / ".env"))
    parser.add_argument("--workspace")
    parser.add_argument("--report")
    parser.add_argument("--exo")
    parser.add_argument("--audio-track", type=int, default=0)
    parser.add_argument("--glossary")
    parser.add_argument("--game-knowledge-store")
    parser.add_argument("--transcript-artifact", action="append", default=[],
                        help="Reuse a complete transcript; repeat for multiple sources")


def _decode_source_specs(raw_specs: list[str]) -> list[dict[str, object]] | None:
    if not raw_specs:
        return None
    source_specs: list[dict[str, object]] = []
    for raw_spec in raw_specs:
        try:
            value = json.loads(raw_spec)
        except json.JSONDecodeError as exc:
            raise SubtitlerError(f"Editorial source specification is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise SubtitlerError("Editorial source specification must be a JSON object")
        source_specs.append(value)
    return source_specs


def _extend_checkpoint(checkpoint: Path, raw_project: str) -> None:
    try:
        request = json.loads(raw_project)
    except json.JSONDecodeError as exc:
        raise SubtitlerError(f"Editorial extension specification is not valid JSON: {exc}") from exc
    if not isinstance(request, dict) or not isinstance(request.get("sources"), list):
        raise SubtitlerError("Editorial extension specification requires an ordered source list")
    project = load_editorial_checkpoint(checkpoint)
    source_specs = request["sources"]
    if not all(isinstance(item, dict) for item in source_specs):
        raise SubtitlerError("Editorial extension sources must be objects")
    existing_count = len(project["sources"])
    if len(source_specs) < existing_count:
        raise SubtitlerError("Editorial extension cannot remove existing analyzed recordings")
    relink_matching_editorial_prefix(project, source_specs[:existing_count])
    new_sources = [
        _parse_source_spec(json.dumps(spec, ensure_ascii=False))
        for spec in source_specs[existing_count:]
    ]
    try:
        options = EditorialProjectOptions(
            title_or_game=str(request["titleOrGame"]),
            objective=str(request["objective"]),
            target_duration_min_ms=round(float(request["targetDurationMinSeconds"]) * 1000),
            target_duration_max_ms=round(float(request["targetDurationMaxSeconds"]) * 1000),
            must_keep_notes=tuple(str(item) for item in request.get("mustKeepNotes", [])),
            de_emphasize_notes=tuple(str(item) for item in request.get("deEmphasizeNotes", [])),
            subtitle_mode=cast(Literal["full", "emphasis"], request.get("subtitleMode", "full")),
            output_locale=cast(Literal["en", "ja"], project.get("output_locale", "en")),
            processing_locale=editorial_locale(project.get("processing_locale", "en")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SubtitlerError("Editorial extension project settings are invalid") from exc
    extend_editorial_project(project, new_sources, options)
    invalidate_editorial_from(project, "global_reconciliation")
    write_editorial_checkpoint(checkpoint, project)
    write_editorial_html(checkpoint.with_suffix(".html"), project)


if __name__ == "__main__":
    raise SystemExit(main())
