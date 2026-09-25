import { TRANSCRIPTION_HIERARCHY, CLEANUP_HIERARCHY, isHostedModelApproved, isHostedModelVerified, recommendedFallbackTranscription, verifiedHostedOptions } from "../../shared/hostedModelCatalog";
import type { CoreWorkflowSettings, EnvStatus, HostedModelVerification } from "./types";

export function isHostedSelectionVerified(settings: CoreWorkflowSettings | null, verification: HostedModelVerification | null): boolean {
  if (!settings?.hosted || !verification) return false;
  const hosted = settings.hosted;
  return isHostedModelVerified(hosted.transcriptionProvider, hosted.transcriptionModel, "transcription", verification)
    && isHostedModelVerified(hosted.fallbackTranscriptionProvider, hosted.fallbackTranscriptionModel, "transcription", verification)
    && isHostedModelVerified(hosted.cleanupProvider, hosted.cleanupModel, "cleanup", verification);
}

export function isHostedSelectionConfigured(settings: CoreWorkflowSettings | null, envStatus: EnvStatus): boolean {
  if (!settings?.hosted || !envStatus.exists) return false;
  const hosted = settings.hosted;
  const hasKey = (provider: "openai" | "gemini" | "dashscope") => provider === "dashscope" ? Boolean(envStatus.keysPresent.DASHSCOPE_API_KEY) : provider === "openai" ? envStatus.keysPresent.OPENAI_API_KEY : envStatus.keysPresent.GEMINI_API_KEY;
  if (hosted.automatic) return TRANSCRIPTION_HIERARCHY.some((x) => hasKey(x.provider)) && CLEANUP_HIERARCHY.some((x) => hasKey(x.provider));
  return hasKey(hosted.transcriptionProvider)
    && hasKey(hosted.fallbackTranscriptionProvider)
    && hasKey(hosted.cleanupProvider)
    && isApprovedHostedSelection(hosted);
}

export function isApprovedHostedSelection(hosted: NonNullable<CoreWorkflowSettings["hosted"]>): boolean {
  return isHostedModelApproved(hosted.transcriptionProvider, hosted.transcriptionModel, "transcription")
    && isHostedModelApproved(hosted.fallbackTranscriptionProvider, hosted.fallbackTranscriptionModel, "transcription")
    && isHostedModelApproved(hosted.cleanupProvider, hosted.cleanupModel, "cleanup");
}

export function matchingHostedOption<T extends { provider: "openai" | "gemini" | "dashscope"; model: string }>(options: T[], provider: "openai" | "gemini" | "dashscope", model: string): T | undefined {
  return options.find((option) => option.provider === provider && option.model === model);
}

export function selectVerifiedHostedSettings(settings: CoreWorkflowSettings, verification: HostedModelVerification): {
  settings: CoreWorkflowSettings;
  transcriptionAvailable: boolean;
  cleanupAvailable: boolean;
} {
  if (!settings.hosted) return { settings, transcriptionAvailable: false, cleanupAvailable: false };
  if (settings.hosted.automatic) {
    const selected = selectConfiguredHostedSettings(settings, { exists: true, keysPresent: {
      OPENAI_API_KEY: verification.openai.keyPresent,
      GEMINI_API_KEY: verification.gemini.keyPresent,
      DASHSCOPE_API_KEY: verification.dashscope?.keyPresent ?? false,
    } });
    const hosted = selected.hosted!;
    return { settings: selected,
      transcriptionAvailable: isHostedModelVerified(hosted.transcriptionProvider, hosted.transcriptionModel, "transcription", verification),
      cleanupAvailable: isHostedModelVerified(hosted.cleanupProvider, hosted.cleanupModel, "cleanup", verification),
    };
  }
  const transcriptionOptions = settings.hosted.automatic
    ? TRANSCRIPTION_HIERARCHY.filter((x) => isHostedModelVerified(x.provider, x.model, "transcription", verification))
    : verifiedHostedOptions(verification, "transcription");
  const cleanupOptions = settings.hosted.automatic
    ? CLEANUP_HIERARCHY.filter((x) => isHostedModelVerified(x.provider, x.model, "cleanup", verification))
    : verifiedHostedOptions(verification, "cleanup");
  const hosted = settings.hosted;
  const selectedTranscription = hosted.automatic ? transcriptionOptions[0] : matchingHostedOption(transcriptionOptions, hosted.transcriptionProvider, hosted.transcriptionModel)
    ?? transcriptionOptions.find((option) => option.provider === hosted.transcriptionProvider)
    ?? transcriptionOptions[0];
  const recommendedFallback = selectedTranscription
    ? recommendedFallbackTranscription(selectedTranscription.provider, selectedTranscription.model)
    : recommendedFallbackTranscription(hosted.transcriptionProvider, hosted.transcriptionModel);
  const distinctFallbackOptions = transcriptionOptions.filter((option) => (
    !selectedTranscription
    || option.provider !== selectedTranscription.provider
    || option.model !== selectedTranscription.model
  ));
  const selectedFallbackTranscription = hosted.automatic ? transcriptionOptions[1] ?? selectedTranscription : matchingHostedOption(
    distinctFallbackOptions,
    hosted.fallbackTranscriptionProvider,
    hosted.fallbackTranscriptionModel
  ) ?? matchingHostedOption(distinctFallbackOptions, recommendedFallback.provider, recommendedFallback.model)
    ?? distinctFallbackOptions.find((option) => option.provider === recommendedFallback.provider)
    ?? distinctFallbackOptions.find((option) => option.provider === hosted.fallbackTranscriptionProvider)
    ?? distinctFallbackOptions[0]
    ?? selectedTranscription;
  const selectedCleanup = hosted.automatic ? cleanupOptions[0] : matchingHostedOption(cleanupOptions, hosted.cleanupProvider, hosted.cleanupModel)
    ?? cleanupOptions.find((option) => option.provider === hosted.cleanupProvider)
    ?? cleanupOptions[0];
  return {
    settings: {
      ...settings,
      hosted: {
        ...hosted,
        transcriptionProvider: selectedTranscription?.provider ?? hosted.transcriptionProvider,
        transcriptionModel: selectedTranscription?.model ?? hosted.transcriptionModel,
        fallbackTranscriptionProvider: selectedFallbackTranscription?.provider ?? hosted.fallbackTranscriptionProvider,
        fallbackTranscriptionModel: selectedFallbackTranscription?.model ?? hosted.fallbackTranscriptionModel,
        cleanupProvider: selectedCleanup?.provider ?? hosted.cleanupProvider,
        cleanupModel: selectedCleanup?.model ?? hosted.cleanupModel
      }
    },
    transcriptionAvailable: transcriptionOptions.length > 0,
    cleanupAvailable: cleanupOptions.length > 0,
  };
}

export function selectConfiguredHostedSettings(settings: CoreWorkflowSettings, env: EnvStatus): CoreWorkflowSettings {
  if (!settings.hosted?.automatic) return settings;
  const hasKey = (provider: string) => provider === "openai" ? env.keysPresent.OPENAI_API_KEY : provider === "gemini" ? env.keysPresent.GEMINI_API_KEY : env.keysPresent.DASHSCOPE_API_KEY;
  const options = TRANSCRIPTION_HIERARCHY.filter((x) => hasKey(x.provider));
  const primary = options[0];
  const fallback = options[1] ?? primary;
  const cleanup = CLEANUP_HIERARCHY.find((x) => hasKey(x.provider));
  if (!primary || !cleanup) return settings;
  const hosted = { ...settings.hosted, transcriptionProvider: primary.provider, transcriptionModel: primary.model,
    fallbackTranscriptionProvider: fallback.provider, fallbackTranscriptionModel: fallback.model,
    cleanupProvider: cleanup.provider, cleanupModel: cleanup.model };
  return JSON.stringify(hosted) === JSON.stringify(settings.hosted) ? settings : { ...settings, hosted };
}
