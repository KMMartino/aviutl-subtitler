"""Independent provider preference orders for hosted subtitle stages."""
import os
from typing import Any

TRANSCRIPTION_ORDER = (("gemini", "gemini-3.8-flash"), ("openai", "gpt-transcribe"),
                       ("dashscope", "qwen-audio-3.1-asr-flash"))
CLEANUP_ORDER = (("openai", "gpt-6-luna"), ("gemini", "gemini-3.6-flash"),
                 ("dashscope", "qwen3.7-flash"))
PROVIDER_KEYS = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "dashscope": "DASHSCOPE_API_KEY"}


def select_hosted_models(config: dict[str, Any]) -> None:
    backend = config.get("backend", {})
    if backend.get("transcriber") == "local-gemma" or not backend.get("auto_select_hosted_models", False):
        return
    available = {p for p, key in PROVIDER_KEYS.items() if os.environ.get(key, "").strip()}
    transcription = [(p, m) for p, m in TRANSCRIPTION_ORDER if p in available]
    if transcription:
        backend["transcriber"], backend["transcription_model"] = transcription[0]
        backend["fallback_transcriber"], backend["fallback_transcription_model"] = transcription[1] if len(transcription) > 1 else ("", "")
    cleanup = config["cleanup"]
    if cleanup["backend"] == "none":
        return
    for provider, model in CLEANUP_ORDER:
        if provider in available:
            cleanup.update(backend=provider, api_model=model,
                           reasoning_effort="low" if provider == "openai" else None,
                           thinking_level="minimal" if provider == "gemini" else None)
            break
