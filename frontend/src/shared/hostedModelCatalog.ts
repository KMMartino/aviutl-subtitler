export type HostedProvider = "openai" | "gemini";
export type HostedRole = "transcription" | "cleanup";
export type HostedEmphasis = "quality" | "balanced" | "speed";

export const APPROVED_MODELS = {
  openaiTranscriptionGpt: "gpt-transcribe",
  openaiCleanup: "gpt-5.4-mini",
  openaiCleanup56Luna: "gpt-5.6-luna",
  gemini: "gemini-3.5-flash",
  gemini37Flash: "gemini-3.7-flash",
  gemini36Flash: "gemini-3.6-flash",
  gemini31Pro: "gemini-3.1-pro-preview",
  gemini31FlashLite: "gemini-3.1-flash-lite"
} as const;

export type HostedCleanupTuning = {
  reasoningEffort: "low" | "medium" | null;
  thinkingLevel: "minimal" | "low" | null;
};

export function hostedCleanupTuning(provider: HostedProvider, model: string): HostedCleanupTuning | null {
  if (provider === "openai" && model === APPROVED_MODELS.openaiCleanup) {
    return { reasoningEffort: "medium", thinkingLevel: null };
  }
  if (provider === "openai" && model === APPROVED_MODELS.openaiCleanup56Luna) {
    return { reasoningEffort: "low", thinkingLevel: null };
  }
  if (provider === "gemini" && model === APPROVED_MODELS.gemini36Flash) {
    return { reasoningEffort: null, thinkingLevel: "minimal" };
  }
  if (provider === "gemini" && model === APPROVED_MODELS.gemini37Flash) {
    return { reasoningEffort: null, thinkingLevel: "low" };
  }
  return null;
}

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
    model: APPROVED_MODELS.openaiTranscriptionGpt,
    label: "OpenAI GPT Transcribe",
    emphasis: "quality",
    blurb: "OpenAI's current high-accuracy file transcription model. Supports dedicated language and glossary hints while preserving this app's existing alignment pipeline.",
    verification: { transcription: "transcriptionGpt" }
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
    model: APPROVED_MODELS.gemini36Flash,
    label: "Gemini 3.6 Flash · Minimal",
    emphasis: "balanced",
    blurb: "Economy Gemini cleanup profile. Minimal thinking preserved the benchmark content with fewer billed tokens and much lower latency than the quality-oriented Luna option.",
    verification: { cleanup: "cleanup" }
  },
  {
    provider: "gemini",
    model: APPROVED_MODELS.gemini37Flash,
    label: "Gemini 3.7 Flash · Low",
    emphasis: "balanced",
    blurb: "New default Gemini transcription profile and higher-quality Gemini cleanup option. Low thinking gave the best 3.7 price-to-performance result and repaired duplicate ASR fragments.",
    verification: { transcription: "transcription37", cleanup: "cleanup37" }
  },
  {
    provider: "gemini",
    model: APPROVED_MODELS.gemini,
    label: "Gemini 3.5 Flash",
    emphasis: "balanced",
    blurb: "Previous Gemini transcription baseline. Retained as a proven alternative and distinct fallback for 3.7 Flash.",
    verification: { transcription: "transcription" }
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
    if (model === APPROVED_MODELS.gemini37Flash) {
      return { provider: "gemini", model: APPROVED_MODELS.gemini };
    }
    return { provider: "gemini", model: APPROVED_MODELS.gemini37Flash };
  }
  return { provider: "openai", model: APPROVED_MODELS.openaiTranscriptionGpt };
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
