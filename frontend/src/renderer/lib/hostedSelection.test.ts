import { describe, expect, it } from "vitest";
import { isHostedSelectionConfigured, matchingHostedOption, selectVerifiedHostedSettings } from "./hostedSelection";
import type { CoreWorkflowSettings, EnvStatus, HostedModelVerification } from "./types";

const settings = { hosted: { transcriptionProvider: "gemini", transcriptionModel: "gemini-3.5-flash", fallbackTranscriptionProvider: "openai", fallbackTranscriptionModel: "gpt-4o-transcribe", cleanupProvider: "openai", cleanupModel: "gpt-5.4-mini", envFile: "" } } as CoreWorkflowSettings;

describe("hosted selection", () => {
  it("requires every selected provider key", () => {
    const env = (openai: boolean, gemini: boolean): EnvStatus => ({ exists: true, keysPresent: { OPENAI_API_KEY: openai, GEMINI_API_KEY: gemini } });
    expect(isHostedSelectionConfigured(settings, env(true, true))).toBe(true);
    expect(isHostedSelectionConfigured(settings, env(true, false))).toBe(false);
    expect(isHostedSelectionConfigured(settings, env(false, true))).toBe(false);
  });

  it("matches an option by provider and model", () => {
    const options = [{ provider: "openai" as const, model: "same", label: "OpenAI" }, { provider: "gemini" as const, model: "same", label: "Gemini" }];
    expect(matchingHostedOption(options, "gemini", "same")?.label).toBe("Gemini");
  });

  it("keeps an available primary selection and chooses available fallback and cleanup models", () => {
    const verification: HostedModelVerification = {
      checkedAt: "now",
      openai: { keyPresent: false, error: "", transcription: false, transcriptionMini: false, cleanup: false, cleanup56Luna: false },
      gemini: { keyPresent: true, error: "", transcription: true, transcription31Pro: true, transcription31FlashLite: false, cleanup: true },
    };

    const result = selectVerifiedHostedSettings(settings, verification);

    expect(result.transcriptionAvailable).toBe(true);
    expect(result.cleanupAvailable).toBe(true);
    expect(result.settings.hosted).toMatchObject({
      transcriptionProvider: "gemini",
      transcriptionModel: "gemini-3.5-flash",
      fallbackTranscriptionProvider: "gemini",
      fallbackTranscriptionModel: "gemini-3.1-pro-preview",
      cleanupProvider: "gemini",
      cleanupModel: "gemini-3.5-flash",
    });
  });
});
