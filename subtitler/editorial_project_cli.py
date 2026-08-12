"""CLI entry point for creating and resuming long-form editorial projects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, cast

from .audio import get_media_duration
from .config import default_config_path, project_root
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
from .editorial_resume import (
    inspect_editorial_resume,
    invalidate_editorial_from,
    relink_matching_editorial_prefix,
)
from .editorial_runner import EditorialRunInterrupted, run_editorial_project
from .errors import SubtitlerError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or resume a suggestion-only editorial map")
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="Create a fingerprinted editorial project")
    initialize.add_argument("--checkpoint", required=True)
    initialize.add_argument("--source", action="append", default=[])
    initialize.add_argument("--source-spec", action="append", default=[])
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--objective", required=True)
    initialize.add_argument("--target-min-sec", type=float, required=True)
    initialize.add_argument("--target-max-sec", type=float, required=True)
    initialize.add_argument("--must-keep", action="append", default=[])
    initialize.add_argument("--de-emphasize", action="append", default=[])
    initialize.add_argument("--subtitle-mode", choices=("full", "emphasis"), default="full")
    initialize.add_argument("--output-locale", choices=("en", "ja"), default="en")

    start = commands.add_parser("start", help="Create a project and immediately run hosted analysis")
    start.add_argument("--checkpoint", required=True)
    start.add_argument("--source", action="append", default=[])
    start.add_argument("--source-spec", action="append", default=[])
    start.add_argument("--title", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--target-min-sec", type=float, required=True)
    start.add_argument("--target-max-sec", type=float, required=True)
    start.add_argument("--must-keep", action="append", default=[])
    start.add_argument("--de-emphasize", action="append", default=[])
    start.add_argument("--subtitle-mode", choices=("full", "emphasis"), default="full")
    start.add_argument("--output-locale", choices=("en", "ja"), default="en")
    _add_run_arguments(start, include_checkpoint=False)

    run = commands.add_parser("run", help="Resume hosted analysis from a checkpoint")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
            title_or_game=args.title,
            objective=args.objective,
            target_duration_min_ms=round(args.target_min_sec * 1000),
            target_duration_max_ms=round(args.target_max_sec * 1000),
            must_keep_notes=tuple(args.must_keep),
            de_emphasize_notes=tuple(args.de_emphasize),
            subtitle_mode=args.subtitle_mode,
            output_locale=args.output_locale,
        ),
    )
    write_editorial_checkpoint(checkpoint, project)
    report = checkpoint.with_suffix(".html")
    write_editorial_html(report, project)
    print(f"Editorial checkpoint: {checkpoint}")
    print(f"Editorial report: {report}")
    return 0


def _run(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint).resolve()
    if getattr(args, "extend_project_spec", None):
        _extend_checkpoint(checkpoint, args.extend_project_spec)
    workspace = Path(args.workspace).resolve() if args.workspace else checkpoint.parent / f"{checkpoint.stem}.files"
    report = Path(args.report).resolve() if args.report else checkpoint.with_suffix(".html")
    executor = HostedEditorialStageExecutor(
        HostedEditorialExecutorOptions(
            config_path=Path(args.config).resolve(),
            env_file=Path(args.env_file).resolve(),
            workspace=workspace,
            pipeline_script=Path(args.pipeline_script).resolve(),
            audio_track=args.audio_track,
            glossary_path=Path(args.glossary).resolve() if args.glossary else None,
            game_knowledge_path=(
                Path(args.game_knowledge_store).resolve()
                if args.game_knowledge_store
                else None
            ),
        )
    )
    run_editorial_project(
        checkpoint,
        executor,
        report_path=report,
        restart_from=getattr(args, "restart_from", None),
        source_specs=_decode_source_specs(getattr(args, "source_spec", [])) or None,
    )
    print(f"Editorial analysis complete: {report}")
    print(f"Editorial AviUtl project: {checkpoint.with_suffix('.exo')}")
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
    pairing_basis = spec.get("pairingBasis")
    if pairing_basis not in {"single", "filename", "resolution", "manual"}:
        raise SubtitlerError("Editorial source specification has an invalid pairingBasis")
    if spec.get("roleConfirmed") is not True:
        raise SubtitlerError("Editorial source roles must be confirmed before analysis")
    return EditorialSourceInput(
        path=visual_path,
        duration_ms=visual_duration_ms,
        audio_path=audio_path,
        visual_path=visual_path,
        audio_duration_ms=audio_duration_ms,
        visual_duration_ms=visual_duration_ms,
        frame_rate=frame_rate,
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
            ),
            default="compatible",
        )
    parser.add_argument("--config", default=str(default_config_path("hosted-long-stream")))
    parser.add_argument("--env-file", default=str(project_root() / ".env"))
    parser.add_argument("--workspace")
    parser.add_argument("--report")
    parser.add_argument("--pipeline-script", default=str(project_root() / "aviutl_subtitle.py"))
    parser.add_argument("--audio-track", type=int, default=1)
    parser.add_argument("--glossary")
    parser.add_argument("--game-knowledge-store")


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
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SubtitlerError("Editorial extension project settings are invalid") from exc
    extend_editorial_project(project, new_sources, options)
    invalidate_editorial_from(project, "global_reconciliation")
    write_editorial_checkpoint(checkpoint, project)
    write_editorial_html(checkpoint.with_suffix(".html"), project)


if __name__ == "__main__":
    raise SystemExit(main())
