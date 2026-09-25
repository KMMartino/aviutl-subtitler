import { describe, expect, it } from "vitest";
import { isHostedSelectionConfigured, matchingHostedOption, selectVerifiedHostedSettings, selectConfiguredHostedSettings } from "./hostedSelection";
import type { CoreWorkflowSettings, EnvStatus, HostedModelVerification } from "./types";

const settings = { hosted: { transcriptionProvider: "gemini", transcriptionModel: "gemini-3.5-flash", fallbackTranscriptionProvider: "openai", fallbackTranscriptionModel: "gpt-transcribe", cleanupProvider: "openai", cleanupModel: "gpt-5.4-mini", envFile: "" } } as CoreWorkflowSettings;

describe("hosted selection", () => {
  it("ranks transcription and cleanup independently for every credential combination", () => {
    for (let mask = 1; mask < 8; mask++) {
      const env: EnvStatus = { exists: true, keysPresent: {
        GEMINI_API_KEY: Boolean(mask & 1), OPENAI_API_KEY: Boolean(mask & 2), DASHSCOPE_API_KEY: Boolean(mask & 4),
      } };
      const automatic = { ...settings, hosted: { ...settings.hosted!, automatic: true } };
      const selected = selectConfiguredHostedSettings(automatic, env);
      expect(selected.hosted?.transcriptionProvider).toBe(mask & 1 ? "gemini" : mask & 2 ? "openai" : "dashscope");
      expect(selected.hosted?.cleanupProvider).toBe(mask & 2 ? "openai" : mask & 1 ? "gemini" : "dashscope");
      expect(isHostedSelectionConfigured(selected, env)).toBe(true);
      expect(selectConfiguredHostedSettings(selected, env)).toBe(selected);
      expect(selectConfiguredHostedSettings(settings, env)).toBe(settings);
    }
  });

  it("keeps automatic verification consistent with backend credential ranking", () => {
    const automatic = { ...settings, hosted: { ...settings.hosted!, automatic: true } };
    const result = selectVerifiedHostedSettings(automatic, {
      checkedAt: "now", openai: { keyPresent: true, error: "", transcriptionGpt: true, cleanup: false, cleanup56Luna: false, cleanup6Luna: true },
      gemini: { keyPresent: true, error: "unavailable", transcription: false, transcription31Pro: false, transcription31FlashLite: false, cleanup: false },
      dashscope: { keyPresent: true, error: "", transcription: true, cleanup: true },
    });
    expect(result.settings.hosted?.transcriptionProvider).toBe("gemini");
    expect(result.transcriptionAvailable).toBe(false);
    expect(result.settings.hosted?.cleanupModel).toBe("gpt-6-luna");
    expect(result.cleanupAvailable).toBe(true);
  });
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
      openai: { keyPresent: false, error: "", transcriptionGpt: false, cleanup: false, cleanup56Luna: false },
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
      cleanupModel: "gemini-3.6-flash",
    });
  });
});
