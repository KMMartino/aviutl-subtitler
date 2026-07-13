"""Typed preparation of CLI inputs before pipeline execution begins."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import default_config_path, load_workflow_config, validate_workflow_config
from .env import load_env_file
from .errors import SubtitlerError
from .run_artifacts import RunArtifactPaths, build_run_artifact_paths


@dataclass(frozen=True)
class CliArguments:
    input: str
    workflow: str
    output: str | None
    config: str | None
    env_file: str
    profile: bool
    audio_track: int | None
    sidecar_dir: str | None
    no_sidecars: bool
    glossary: str | None
    no_glossary: bool


@dataclass(frozen=True)
class RunContext:
    args: CliArguments
    input_path: Path
    output_path: Path
    config_path: Path
    config: dict[str, Any]
    env_path: Path
    loaded_env_keys: list[str]
    sidecars_enabled: bool
    diagnostics_enabled: bool
    artifacts: RunArtifactPaths


def prepare_run_context(args: CliArguments, *, cwd: Path | None = None) -> RunContext:
    """Validate and normalize run inputs without executing media/model stages."""
    input_path = Path(args.input)
    if not input_path.exists():
        raise SubtitlerError(f"input file not found: {input_path}")

    config_path = Path(args.config) if args.config else default_config_path(args.workflow)
    config = load_workflow_config(args.workflow, Path(args.config) if args.config else None)
    if args.audio_track is not None:
        config["audio"]["track"] = args.audio_track
    if args.profile:
        config["diagnostics"]["profile"] = True
    validate_workflow_config(config, workflow=args.workflow)

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = (cwd or Path.cwd()) / env_path
    loaded_env_keys = load_env_file(env_path)
    configure_alignment_offline_mode(config["alignment"])

    output_path = Path(args.output) if args.output else default_output_path(input_path, args.workflow)
    sidecars_enabled = not args.no_sidecars
    artifacts = build_run_artifact_paths(
        input_path,
        output_path,
        enabled=sidecars_enabled,
        directory=Path(args.sidecar_dir) if args.sidecar_dir else None,
    )
    diagnostics_enabled = sidecars_enabled and bool(config["diagnostics"]["profile"])
    return RunContext(
        args=args,
        input_path=input_path,
        output_path=output_path,
        config_path=config_path,
        config=config,
        env_path=env_path,
        loaded_env_keys=loaded_env_keys,
        sidecars_enabled=sidecars_enabled,
        diagnostics_enabled=diagnostics_enabled,
        artifacts=artifacts,
    )


def configure_alignment_offline_mode(alignment: dict[str, Any]) -> bool:
    """Enable Hub offline mode only for a confirmed local model snapshot."""
    model_path = Path(alignment["model"])
    snapshot_present = (
        model_path.is_dir()
        and (model_path / "config.json").is_file()
        and ((model_path / "model.safetensors").is_file() or (model_path / "pytorch_model.bin").is_file())
        and (model_path / "tokenizer_config.json").is_file()
    )
    enabled = bool(alignment.get("offline_model_cache", False) and snapshot_present)
    if enabled:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return enabled


def default_output_path(input_path: Path, workflow: str) -> Path:
    suffix = {
        "local": "",
        "hosted": "-hosted",
        "local-long-stream": "-long-stream-local",
        "hosted-long-stream": "-long-stream-hosted",
    }[workflow]
    return input_path.with_name(f"{input_path.stem}{suffix}.exo")
