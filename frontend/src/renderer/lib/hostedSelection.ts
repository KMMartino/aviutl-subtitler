import { isHostedModelApproved, isHostedModelVerified, recommendedFallbackTranscription, verifiedHostedOptions } from "../../shared/hostedModelCatalog";
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
  const hasKey = (provider: "openai" | "gemini") => provider === "openai" ? envStatus.keysPresent.OPENAI_API_KEY : envStatus.keysPresent.GEMINI_API_KEY;
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

export function matchingHostedOption<T extends { provider: "openai" | "gemini"; model: string }>(options: T[], provider: "openai" | "gemini", model: string): T | undefined {
  return options.find((option) => option.provider === provider && option.model === model);
}

export function selectVerifiedHostedSettings(settings: CoreWorkflowSettings, verification: HostedModelVerification): {
  settings: CoreWorkflowSettings;
  transcriptionAvailable: boolean;
  cleanupAvailable: boolean;
} {
  if (!settings.hosted) return { settings, transcriptionAvailable: false, cleanupAvailable: false };
  const transcriptionOptions = verifiedHostedOptions(verification, "transcription");
  const cleanupOptions = verifiedHostedOptions(verification, "cleanup");
  const hosted = settings.hosted;
  const selectedTranscription = matchingHostedOption(transcriptionOptions, hosted.transcriptionProvider, hosted.transcriptionModel)
    ?? transcriptionOptions.find((option) => option.provider === hosted.transcriptionProvider)
    ?? transcriptionOptions[0];
  const recommendedFallback = selectedTranscription
    ? recommendedFallbackTranscription(selectedTranscription.provider, selectedTranscription.model)
    : recommendedFallbackTranscription(hosted.transcriptionProvider, hosted.transcriptionModel);
  const selectedFallbackTranscription = matchingHostedOption(
    transcriptionOptions,
    hosted.fallbackTranscriptionProvider,
    hosted.fallbackTranscriptionModel
  ) ?? matchingHostedOption(transcriptionOptions, recommendedFallback.provider, recommendedFallback.model)
    ?? transcriptionOptions.find((option) => option.provider === recommendedFallback.provider)
    ?? transcriptionOptions.find((option) => option.provider === hosted.fallbackTranscriptionProvider)
    ?? transcriptionOptions[0];
  const selectedCleanup = matchingHostedOption(cleanupOptions, hosted.cleanupProvider, hosted.cleanupModel)
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
