export type HostedProvider = "openai" | "gemini";
export type HostedRole = "transcription" | "cleanup";
export type HostedEmphasis = "quality" | "balanced" | "speed";

export const APPROVED_MODELS = {
  openaiTranscription: "gpt-4o-transcribe",
  openaiTranscriptionMini: "gpt-4o-mini-transcribe",
  openaiCleanup: "gpt-5.4-mini",
  openaiCleanup56Luna: "gpt-5.6-luna",
  gemini: "gemini-3.5-flash",
  gemini31Pro: "gemini-3.1-pro-preview",
  gemini31FlashLite: "gemini-3.1-flash-lite"
} as const;

export type HostedCleanupTuning = {
  reasoningEffort: "low" | "medium" | null;
  thinkingLevel: "minimal" | null;
};

export function hostedCleanupTuning(provider: HostedProvider, model: string): HostedCleanupTuning | null {
  if (provider === "openai" && model === APPROVED_MODELS.openaiCleanup) {
    return { reasoningEffort: "medium", thinkingLevel: null };
  }
  if (provider === "openai" && model === APPROVED_MODELS.openaiCleanup56Luna) {
    return { reasoningEffort: "low", thinkingLevel: null };
  }
  if (provider === "gemini" && model === APPROVED_MODELS.gemini) {
    return { reasoningEffort: null, thinkingLevel: "minimal" };
  }
  return null;
}

export const OPENAI_TRANSCRIPTION_MODEL_ALIASES: Record<string, string[]> = {
  [APPROVED_MODELS.openaiTranscription]: [APPROVED_MODELS.openaiTranscription],
  [APPROVED_MODELS.openaiTranscriptionMini]: [
    APPROVED_MODELS.openaiTranscriptionMini,
    "gpt-4o-mini-transcribe-2025-12-15"
  ]
};

export type HostedOption = {
  provider: HostedProvider;
  model: string;
  label: string;
  emphasis: HostedEmphasis;
  blurb: string;
};

type HostedModel = HostedOption & {
  verification: Partial<Record<HostedRole, string>>;
};

export const HOSTED_MODELS: HostedModel[] = [
  {
    provider: "openai",
    model: APPROVED_MODELS.openaiTranscription,
    label: "OpenAI GPT-4o Transcribe",
    emphasis: "quality",
    blurb: "Higher-accuracy OpenAI speech-to-text model. Medium speed, audio and text input, text output. Best OpenAI choice here when transcription quality matters.",
    verification: { transcription: "transcription" }
  },
  {
    provider: "openai",
    model: APPROVED_MODELS.openaiTranscriptionMini,
    label: "OpenAI GPT-4o mini Transcribe",
    emphasis: "speed",
    blurb: "Fast, lower-cost OpenAI speech-to-text model. Audio and text input, text output. A practical choice when throughput matters more than maximum accuracy.",
    verification: { transcription: "transcriptionMini" }
  },
  {
    provider: "openai",
    model: APPROVED_MODELS.openaiCleanup,
    label: "OpenAI GPT-5.4 mini · Medium",
    emphasis: "quality",
    blurb: "High-accuracy tested profile. Medium reasoning repaired difficult cleanup defects while preserving title content on the benchmark.",
    verification: { cleanup: "cleanup" }
  },
  {
    provider: "openai",
    model: APPROVED_MODELS.openaiCleanup56Luna,
    label: "OpenAI GPT-5.6 Luna · Low",
    emphasis: "speed",
    blurb: "Budget-quality tested profile. Low reasoning provided meaningful cleanup while preserving semantic content.",
    verification: { cleanup: "cleanup56Luna" }
  },
  {
    provider: "gemini",
    model: APPROVED_MODELS.gemini,
    label: "Gemini 3.5 Flash · Minimal",
    emphasis: "balanced",
    blurb: "Tested Gemini cleanup profile. Minimal thinking was faster and followed the cleanup format more reliably than low or medium.",
    verification: { transcription: "transcription", cleanup: "cleanup" }
  },
  {
    provider: "gemini",
    model: APPROVED_MODELS.gemini31Pro,
    label: "Gemini 3.1 Pro Preview",
    emphasis: "quality",
    blurb: "Google's highest-intelligence option in this selector. Accepts audio and can transcribe and analyze it, but is slower, more expensive, and currently a preview model.",
    verification: { transcription: "transcription31Pro" }
  },
  {
    provider: "gemini",
    model: APPROVED_MODELS.gemini31FlashLite,
    label: "Gemini 3.1 Flash-Lite",
    emphasis: "speed",
    blurb: "Fastest and lowest-cost Gemini option here. Accepts audio and is explicitly documented for transcription, but has less reasoning depth than Pro or 3.5 Flash.",
    verification: { transcription: "transcription31FlashLite" }
  }
];

export function hostedOptions(role: HostedRole): HostedOption[] {
  return HOSTED_MODELS.filter((model) => role in model.verification);
}

export function approvedHostedModels(provider: HostedProvider, role: HostedRole): string[] {
  return hostedOptions(role).filter((model) => model.provider === provider).map((model) => model.model);
}

export function recommendedFallbackTranscription(
  provider: HostedProvider,
  model: string,
): { provider: HostedProvider; model: string } {
  if (provider === "gemini") {
    if (model === APPROVED_MODELS.gemini31Pro) {
      return { provider: "gemini", model: APPROVED_MODELS.gemini };
    }
    if (model === APPROVED_MODELS.gemini31FlashLite) {
      return { provider: "gemini", model: APPROVED_MODELS.gemini };
    }
    return { provider: "gemini", model: APPROVED_MODELS.gemini31Pro };
  }
  if (model === APPROVED_MODELS.openaiTranscription) {
    return { provider: "openai", model: APPROVED_MODELS.openaiTranscriptionMini };
  }
  return { provider: "openai", model: APPROVED_MODELS.openaiTranscription };
}

export function isHostedModelApproved(provider: HostedProvider, model: string, role: HostedRole): boolean {
  return approvedHostedModels(provider, role).includes(model);
}

export function isHostedModelVerified(
  provider: HostedProvider,
  model: string,
  role: HostedRole,
  verification: Record<HostedProvider, Record<string, unknown>>,
): boolean {
  const item = HOSTED_MODELS.find((candidate) => (
    candidate.provider === provider
    && candidate.model === model
    && role in candidate.verification
  ));
  const key = item?.verification[role];
  return Boolean(key && verification[provider][key]);
}

export function verifiedHostedOptions(
  verification: Record<HostedProvider, Record<string, unknown>>,
  role: HostedRole,
): Array<{ provider: HostedProvider; model: string }> {
  return hostedOptions(role)
    .filter((option) => isHostedModelVerified(option.provider, option.model, role, verification))
    .map(({ provider, model }) => ({ provider, model }));
}
