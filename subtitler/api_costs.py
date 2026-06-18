"""Cost estimation and pricing helpers for hosted API benchmarks."""

from __future__ import annotations

from dataclasses import dataclass


GEMINI_AUDIO_TOKENS_PER_SECOND = 32.0
TRANSCRIPT_OUTPUT_TOKENS_PER_SPEECH_SECOND = 12.0
CLEANUP_TOKEN_MULTIPLIER = 3.0
OPENAI_GPT4O_TRANSCRIBE_ESTIMATED_USD_PER_MINUTE = 0.006


@dataclass(frozen=True)
class TokenPrices:
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    audio_input_per_1m: float | None = None
    estimated_per_minute: float | None = None


GEMINI_PRICES: dict[str, TokenPrices] = {
    "gemini-3.5-flash": TokenPrices(input_per_1m=1.50, output_per_1m=9.00, audio_input_per_1m=1.50),
    "gemini-3-flash-preview": TokenPrices(input_per_1m=0.50, output_per_1m=3.00, audio_input_per_1m=1.00),
    "gemini-3.1-pro-preview": TokenPrices(input_per_1m=2.00, output_per_1m=12.00, audio_input_per_1m=2.00),
    "gemini-3.1-flash-lite": TokenPrices(input_per_1m=0.10, output_per_1m=0.40, audio_input_per_1m=0.30),
    "gemini-2.5-flash": TokenPrices(input_per_1m=0.30, output_per_1m=2.50, audio_input_per_1m=1.00),
}

OPENAI_PRICES: dict[str, TokenPrices] = {
    "gpt-4o-transcribe": TokenPrices(
        input_per_1m=2.50,
        output_per_1m=10.00,
        audio_input_per_1m=6.00,
        estimated_per_minute=OPENAI_GPT4O_TRANSCRIBE_ESTIMATED_USD_PER_MINUTE,
    ),
    "gpt-4o-mini-transcribe": TokenPrices(
        input_per_1m=1.25,
        output_per_1m=5.00,
        audio_input_per_1m=3.00,
        estimated_per_minute=0.003,
    ),
    "gpt-4o-mini-transcribe-2025-12-15": TokenPrices(
        input_per_1m=1.25,
        output_per_1m=5.00,
        audio_input_per_1m=3.00,
        estimated_per_minute=0.003,
    ),
    "gpt-5.5": TokenPrices(input_per_1m=5.00, output_per_1m=30.00),
    "gpt-5.4-mini": TokenPrices(input_per_1m=0.75, output_per_1m=4.50),
}


def model_prices(provider: str, model: str) -> TokenPrices:
    if provider == "gemini":
        return GEMINI_PRICES.get(model, TokenPrices(input_per_1m=1.50, output_per_1m=9.00, audio_input_per_1m=1.50))
    if provider == "openai":
        return OPENAI_PRICES.get(model, TokenPrices(input_per_1m=5.00, output_per_1m=30.00))
    return TokenPrices()


def token_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    audio_input_tokens: int = 0,
) -> float:
    prices = model_prices(provider, model)
    text_input_tokens = max(0, input_tokens - audio_input_tokens)
    audio_rate = prices.audio_input_per_1m if prices.audio_input_per_1m is not None else prices.input_per_1m
    return (
        text_input_tokens * prices.input_per_1m / 1_000_000
        + audio_input_tokens * audio_rate / 1_000_000
        + output_tokens * prices.output_per_1m / 1_000_000
    )


def estimate_transcription_cost(provider: str, model: str, speech_seconds: float) -> float:
    if provider == "gemini":
        prices = model_prices(provider, model)
        audio_tokens = int(round(max(0.0, speech_seconds) * GEMINI_AUDIO_TOKENS_PER_SECOND))
        output_tokens = int(round(max(0.0, speech_seconds) * TRANSCRIPT_OUTPUT_TOKENS_PER_SPEECH_SECOND))
        return token_cost(provider, model, input_tokens=audio_tokens, output_tokens=output_tokens, audio_input_tokens=audio_tokens)
    if provider == "openai":
        prices = model_prices(provider, model)
        if prices.estimated_per_minute is not None:
            return max(0.0, speech_seconds) / 60.0 * prices.estimated_per_minute
    return 0.0


def estimate_cleanup_cost(provider: str, model: str, speech_seconds: float) -> float:
    if provider not in {"gemini", "openai"}:
        return 0.0
    transcript_tokens = int(round(max(0.0, speech_seconds) * TRANSCRIPT_OUTPUT_TOKENS_PER_SPEECH_SECOND))
    input_tokens = int(round(transcript_tokens * CLEANUP_TOKEN_MULTIPLIER))
    output_tokens = int(round(transcript_tokens * 1.5))
    return token_cost(provider, model, input_tokens=input_tokens, output_tokens=output_tokens)


def estimate_run_cost(
    *,
    transcriber_backend: str,
    transcription_model: str,
    cleanup_backend: str,
    cleanup_model: str,
    speech_seconds: float,
) -> float:
    return estimate_transcription_cost(transcriber_backend, transcription_model, speech_seconds) + estimate_cleanup_cost(
        cleanup_backend,
        cleanup_model,
        speech_seconds,
    )
