"""Workflow config loading for the simplified CLI."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from .errors import SubtitlerError


WORKFLOWS = {"local", "hosted", "local-long-stream", "hosted-long-stream"}
OPENAI_TRANSCRIPTION_MODEL_ALIASES = {
    "gpt-4o-mini-transcribe": {
        "gpt-4o-mini-transcribe",
        "gpt-4o-mini-transcribe-2025-12-15",
    },
    "gpt-4o-transcribe": {
        "gpt-4o-transcribe",
    },
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_config_path(workflow: str) -> Path:
    if workflow not in WORKFLOWS:
        raise SubtitlerError(f"Unknown workflow: {workflow}")
    return project_root() / "configs" / f"{workflow}.json"


def canonical_openai_transcription_model(model: str) -> str:
    for canonical, aliases in OPENAI_TRANSCRIPTION_MODEL_ALIASES.items():
        if model in aliases:
            return canonical
    return model


def openai_transcription_aliases(model: str) -> set[str]:
    canonical = canonical_openai_transcription_model(model)
    return set(OPENAI_TRANSCRIPTION_MODEL_ALIASES.get(canonical, {model}))


def openai_model_available(configured_model: str, available_names: list[str] | set[str]) -> str | None:
    available = set(available_names)
    for alias in openai_transcription_aliases(configured_model):
        if alias in available:
            return alias
    return None


def load_workflow_config(workflow: str, explicit_path: Path | None = None) -> dict[str, Any]:
    path = explicit_path or default_config_path(workflow)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SubtitlerError(f"Workflow config not found: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise SubtitlerError(f"Could not read workflow config {path}: {exc}") from exc
    try:
        data = json.loads(raw, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise SubtitlerError(
            f"Invalid JSON in workflow config {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except ValueError as exc:
        raise SubtitlerError(f"Invalid JSON in workflow config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SubtitlerError(f"Workflow config must be a JSON object: {path}")
    merged = _defaults()
    _deep_update(merged, data)
    merged.setdefault("workflow", {})["name"] = workflow
    return merged


def validate_workflow_config(config: dict[str, Any], *, workflow: str, check_paths: bool = True) -> None:
    if workflow not in WORKFLOWS:
        raise SubtitlerError(f"Unknown workflow: {workflow}")

    backend = _section(config, "backend")
    audio = _section(config, "audio")
    workflow_cfg = _section(config, "workflow")
    vad = _section(config, "vad")
    alignment = _section(config, "alignment")
    cleanup = _section(config, "cleanup")
    subtitles = _section(config, "subtitles")
    exo = _section(config, "exo")
    cost = _section(config, "cost")
    additional_settings = _section(config, "additional_settings")
    diagnostics = _section(config, "diagnostics")

    _choice(backend.get("name"), {"existing-pipeline"}, "backend.name")
    _choice(backend.get("transcriber"), {"local-gemma", "gemini", "openai"}, "backend.transcriber")
    _non_empty_string(backend.get("language"), "backend.language")
    _optional_string(backend.get("model"), "backend.model")
    if "transcription_model" in backend:
        _optional_string(backend.get("transcription_model"), "backend.transcription_model")
    _port(backend.get("server_port"), "backend.server_port")
    _int_min(backend.get("n_gpu_layers"), -1, "backend.n_gpu_layers")
    _int_min(backend.get("ctx_size"), 1, "backend.ctx_size")
    _int_min(backend.get("audio_prep_workers"), 1, "backend.audio_prep_workers")
    _optional_int_min(backend.get("transcription_workers"), 1, "backend.transcription_workers")
    _int_min(backend.get("transcription_max_split_depth"), 0, "backend.transcription_max_split_depth")
    _optional_string(backend.get("fallback_transcriber"), "backend.fallback_transcriber")
    _optional_string(backend.get("fallback_transcription_model"), "backend.fallback_transcription_model")
    _optional_string(backend.get("mmproj"), "backend.mmproj")
    _optional_string(backend.get("llama_server"), "backend.llama_server")
    _optional_string(backend.get("spec_draft_model"), "backend.spec_draft_model")
    _int_min(backend.get("spec_draft_n_max"), 1, "backend.spec_draft_n_max")
    _int_min(audio.get("track"), 0, "audio.track")
    _choice(workflow_cfg.get("mode"), {"full", "long-stream"}, "workflow.mode")
    _int_min(workflow_cfg.get("long_stream_min_chunks"), 0, "workflow.long_stream_min_chunks")
    ratio = workflow_cfg.get("long_stream_selection_ratio")
    if ratio is not None:
        numeric_ratio = _finite_number(ratio, "workflow.long_stream_selection_ratio")
        if not 0.0 <= numeric_ratio <= 1.0:
            raise SubtitlerError("workflow.long_stream_selection_ratio must be between 0 and 1")
    _positive(vad.get("max_chunk_sec"), "vad.max_chunk_sec")
    _positive(vad.get("min_speech_sec"), "vad.min_speech_sec")
    _int_min(vad.get("min_silence_ms"), 1, "vad.min_silence_ms")
    _int_min(vad.get("speech_pad_ms"), 0, "vad.speech_pad_ms")
    _non_empty_string(alignment.get("model"), "alignment.model")
    _non_empty_string(alignment.get("device"), "alignment.device")
    _int_min(alignment.get("max_split_depth"), 0, "alignment.max_split_depth")
    _boolean(alignment.get("offline_model_cache"), "alignment.offline_model_cache")
    _optional_int_min(alignment.get("workers"), 1, "alignment.workers")
    _optional_int_min(alignment.get("torch_threads"), 1, "alignment.torch_threads")
    _int_min(alignment.get("emission_batch_size"), 1, "alignment.emission_batch_size")
    _choice(cleanup.get("backend"), {"none", "local-llama", "gemini", "openai"}, "cleanup.backend")
    _optional_string(cleanup.get("model"), "cleanup.model")
    _optional_string(cleanup.get("api_model"), "cleanup.api_model")
    if cleanup.get("reasoning_effort") is not None:
        _choice(
            cleanup.get("reasoning_effort"),
            {"none", "minimal", "low", "medium", "high", "xhigh"},
            "cleanup.reasoning_effort",
        )
    if cleanup.get("thinking_level") is not None:
        _choice(cleanup.get("thinking_level"), {"minimal", "low", "medium", "high"}, "cleanup.thinking_level")
    _optional_string(cleanup.get("llama_server"), "cleanup.llama_server")
    _port(cleanup.get("server_port"), "cleanup.server_port")
    _int_min(cleanup.get("ctx_size"), 1, "cleanup.ctx_size")
    _optional_int_min(cleanup.get("window_subtitles"), 1, "cleanup.window_subtitles")
    _optional_float_min(cleanup.get("group_min_sec"), 0.001, "cleanup.group_min_sec")
    _optional_float_min(cleanup.get("group_duration_divisor"), 0.001, "cleanup.group_duration_divisor")
    _optional_float_min(cleanup.get("group_max_sec"), 0.001, "cleanup.group_max_sec")
    if (
        cleanup.get("group_min_sec") is not None
        and cleanup.get("group_max_sec") is not None
        and float(cleanup["group_min_sec"]) > float(cleanup["group_max_sec"])
    ):
        raise SubtitlerError("cleanup.group_min_sec must be less than or equal to cleanup.group_max_sec")
    _optional_int_min(cleanup.get("workers"), 1, "cleanup.workers")
    _boolean(cleanup.get("skip_final_review"), "cleanup.skip_final_review")
    _boolean(cleanup.get("llm_split_planning"), "cleanup.llm_split_planning")
    _optional_string(cleanup.get("spec_draft_model"), "cleanup.spec_draft_model")
    _int_min(cleanup.get("spec_draft_n_max"), 1, "cleanup.spec_draft_n_max")
    _int_min(subtitles.get("max_chars"), 1, "subtitles.max_chars")
    _positive(subtitles.get("min_duration"), "subtitles.min_duration")
    _positive(subtitles.get("max_duration"), "subtitles.max_duration")
    if subtitles["min_duration"] > subtitles["max_duration"]:
        raise SubtitlerError("subtitles.min_duration must be <= subtitles.max_duration")
    _non_negative(subtitles.get("gap_threshold"), "subtitles.gap_threshold")
    _non_negative(subtitles.get("regroup_gap_sec"), "subtitles.regroup_gap_sec")
    _non_negative(subtitles.get("chain_lead_in_sec"), "subtitles.chain_lead_in_sec")
    _optional_int_min(subtitles.get("chain_split_workers"), 1, "subtitles.chain_split_workers")
    _boolean(diagnostics.get("profile"), "diagnostics.profile")
    _boolean(diagnostics.get("llm_split_diagnostics"), "diagnostics.llm_split_diagnostics")
    _int_min(exo.get("width"), 1, "exo.width")
    _int_min(exo.get("height"), 1, "exo.height")
    _int_min(exo.get("fps"), 1, "exo.fps")
    _int_min(exo.get("font_size"), 1, "exo.font_size")
    _non_empty_string(exo.get("font"), "exo.font")
    _finite_number(exo.get("y_position"), "exo.y_position")
    _non_negative(cost.get("max_estimated_api_cost_usd"), "cost.max_estimated_api_cost_usd")
    _boolean(cost.get("allow_api_spend"), "cost.allow_api_spend")
    _boolean(cost.get("estimate_cost_only"), "cost.estimate_cost_only")
    _boolean(additional_settings.get("youtube_chapters"), "additional_settings.youtube_chapters")

    expected_mode = "long-stream" if workflow.endswith("-long-stream") else "full"
    is_hosted = workflow.startswith("hosted")
    if additional_settings["youtube_chapters"] and workflow != "hosted":
        raise SubtitlerError("additional_settings.youtube_chapters is only supported by the hosted short workflow")
    valid_pairing = (
        backend["transcriber"] in {"gemini", "openai"} and cleanup["backend"] in {"gemini", "openai"}
        if is_hosted
        else backend["transcriber"] == "local-gemma" and cleanup["backend"] == "local-llama"
    )
    if workflow_cfg["mode"] != expected_mode or not valid_pairing:
        raise SubtitlerError(
            "Workflow config mismatch for "
            f"{workflow}: got mode/transcriber/cleanup "
            f"{(workflow_cfg['mode'], backend['transcriber'], cleanup['backend'])}"
        )

    if backend["transcriber"] == "local-gemma":
        _non_empty_string(backend.get("model"), "backend.model")
        if check_paths:
            _existing_path(backend.get("model"), "backend.model")
            if backend.get("mmproj"):
                _existing_path(backend.get("mmproj"), "backend.mmproj")
            if backend.get("llama_server"):
                _existing_path(backend.get("llama_server"), "backend.llama_server")
            if backend.get("spec_draft_model"):
                _existing_path(backend.get("spec_draft_model"), "backend.spec_draft_model")
    else:
        _non_empty_string(backend.get("transcription_model"), "backend.transcription_model")

    if cleanup["backend"] == "local-llama":
        _non_empty_string(cleanup.get("model"), "cleanup.model")
        if check_paths:
            _existing_path(cleanup.get("model"), "cleanup.model")
            if cleanup.get("llama_server"):
                _existing_path(cleanup.get("llama_server"), "cleanup.llama_server")
            if cleanup.get("spec_draft_model"):
                _existing_path(cleanup.get("spec_draft_model"), "cleanup.spec_draft_model")
    elif cleanup["backend"] in {"gemini", "openai"}:
        _non_empty_string(cleanup.get("api_model"), "cleanup.api_model")
        if (
            cleanup["backend"] == "gemini"
            and cleanup.get("api_model") == "gemini-3.1-pro-preview"
            and cleanup.get("thinking_level") == "minimal"
        ):
            raise SubtitlerError("gemini-3.1-pro-preview does not support cleanup.thinking_level=minimal")

    if is_hosted:
        approved_transcription = {
            "openai": set().union(*OPENAI_TRANSCRIPTION_MODEL_ALIASES.values()),
            "gemini": {"gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite"},
        }
        approved_cleanup = {
            "openai": {
                "gpt-5.4-mini",
                "gpt-5.5",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
            },
            "gemini": {"gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite"},
        }
        if backend.get("transcription_model") not in approved_transcription[backend["transcriber"]]:
            raise SubtitlerError(
                f"Unsupported hosted transcription model for {backend['transcriber']}: "
                f"{backend.get('transcription_model')}"
            )
        fallback_transcriber = str(backend.get("fallback_transcriber") or "").strip()
        fallback_model = str(backend.get("fallback_transcription_model") or "").strip()
        if fallback_transcriber or fallback_model:
            _choice(fallback_transcriber, {"gemini", "openai"}, "backend.fallback_transcriber")
            _non_empty_string(fallback_model, "backend.fallback_transcription_model")
            if fallback_model not in approved_transcription[fallback_transcriber]:
                raise SubtitlerError(
                    f"Unsupported hosted fallback transcription model for {fallback_transcriber}: "
                    f"{fallback_model}"
                )
        if cleanup.get("api_model") not in approved_cleanup[cleanup["backend"]]:
            raise SubtitlerError(
                f"Unsupported hosted cleanup model for {cleanup['backend']}: {cleanup.get('api_model')}"
            )


def _defaults() -> dict[str, Any]:
    return {
        "backend": {
            "name": "existing-pipeline",
            "transcriber": "local-gemma",
            "model": "",
            "mmproj": "",
            "llama_server": "",
            "server_port": 8081,
            "language": "ja",
            "n_gpu_layers": -1,
            "ctx_size": 8192,
            "audio_prep_workers": 2,
            "transcription_workers": None,
            "transcription_max_split_depth": 2,
            "fallback_transcriber": "",
            "fallback_transcription_model": "",
            "spec_draft_model": "",
            "spec_draft_n_max": 3,
        },
        "audio": {"track": 1},
        "workflow": {
            "mode": "full",
            "long_stream_selection_ratio": None,
            "long_stream_min_chunks": 1,
        },
        "vad": {
            "max_chunk_sec": 30.0,
            "min_speech_sec": 0.25,
            "min_silence_ms": 400,
            "speech_pad_ms": 200,
        },
        "alignment": {
            "model": "MahmoudAshraf/mms-300m-1130-forced-aligner",
            "device": "auto",
            "max_split_depth": 4,
            "offline_model_cache": False,
            "workers": None,
            "torch_threads": None,
            "emission_batch_size": 4,
        },
        "cleanup": {
            "backend": "none",
            "model": "",
            "api_model": "",
            "reasoning_effort": None,
            "thinking_level": None,
            "llama_server": "",
            "server_port": 8082,
            "ctx_size": 4096,
            "window_subtitles": None,
            "group_min_sec": 60.0,
            "group_duration_divisor": 2.0,
            "group_max_sec": 600.0,
            "workers": None,
            "skip_final_review": False,
            "llm_split_planning": False,
            "spec_draft_model": "",
            "spec_draft_n_max": 3,
        },
        "subtitles": {
            "max_chars": 40,
            "min_duration": 0.40,
            "max_duration": 6.0,
            "gap_threshold": 0.25,
            "regroup_gap_sec": 0.5,
            "chain_lead_in_sec": 0.08,
            "chain_split_workers": None,
        },
        "diagnostics": {
            "profile": True,
            "llm_split_diagnostics": True,
        },
        "exo": {
            "width": 2560,
            "height": 1440,
            "fps": 60,
            "font": "M+ 2p heavy",
            "font_size": 60,
            "y_position": 717.0,
        },
        "cost": {
            "max_estimated_api_cost_usd": 5.0,
            "allow_api_spend": False,
            "estimate_cost_only": False,
        },
        "additional_settings": {
            "youtube_chapters": False,
        },
    }


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise SubtitlerError(f"{name} must be a config object")
    return value


def _choice(value: Any, choices: set[str], field: str) -> None:
    if value not in choices:
        raise SubtitlerError(f"{field} must be one of: {', '.join(sorted(choices))}")


def _non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SubtitlerError(f"{field} must be a non-empty string")


def _optional_string(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise SubtitlerError(f"{field} must be a string")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SubtitlerError(f"{field} must be a finite number")
    return float(value)


def _positive(value: Any, field: str) -> None:
    numeric = _finite_number(value, field)
    if numeric <= 0:
        raise SubtitlerError(f"{field} must be a positive number")


def _non_negative(value: Any, field: str) -> None:
    numeric = _finite_number(value, field)
    if numeric < 0:
        raise SubtitlerError(f"{field} must be a non-negative number")


def _int_min(value: Any, minimum: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SubtitlerError(f"{field} must be an integer >= {minimum}")


def _optional_int_min(value: Any, minimum: int, field: str) -> None:
    if value is not None:
        _int_min(value, minimum, field)


def _optional_float_min(value: Any, minimum: float, field: str) -> None:
    if value is not None and _finite_number(value, field) < minimum:
        raise SubtitlerError(f"{field} must be a number >= {minimum}")


def _port(value: Any, field: str) -> None:
    _int_min(value, 1, field)
    if value > 65535:
        raise SubtitlerError(f"{field} must be an integer between 1 and 65535")


def _boolean(value: Any, field: str) -> None:
    if not isinstance(value, bool):
        raise SubtitlerError(f"{field} must be a boolean")


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"nonstandard numeric constant {value} is not allowed")


def _existing_path(value: Any, field: str) -> None:
    path = Path(str(value))
    if not path.exists():
        raise SubtitlerError(f"{field} does not exist: {path}")
