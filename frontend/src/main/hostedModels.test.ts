import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { APPROVED_MODELS, readEnvValues, verifyHostedModels } from "./hostedModels";
import { hostedCleanupTuning, hostedOptions, recommendedFallbackTranscription } from "../shared/hostedModelCatalog";

const files: string[] = [];

afterEach(() => {
  for (const file of files.splice(0)) fs.rmSync(file, { force: true });
  globalThis.fetch = originalFetch;
});

const originalFetch = globalThis.fetch;

describe("hosted model verification helpers", () => {
  it("uses the intentionally restricted model set", () => {
    expect(APPROVED_MODELS).toEqual({
      openaiTranscriptionGpt: "gpt-transcribe",
      openaiCleanup: "gpt-5.4-mini",
      openaiCleanup56Luna: "gpt-5.6-luna",
      gemini: "gemini-3.5-flash",
      gemini37Flash: "gemini-3.7-flash",
      gemini36Flash: "gemini-3.6-flash",
      gemini31Pro: "gemini-3.1-pro-preview",
      gemini31FlashLite: "gemini-3.1-flash-lite"
    });
  });

  it("offers the four benchmark-selected cleanup profiles", () => {
    expect(hostedOptions("cleanup").map(({ provider, model }) => `${provider}:${model}`)).toEqual([
      "openai:gpt-5.4-mini",
      "openai:gpt-5.6-luna",
      "gemini:gemini-3.6-flash",
      "gemini:gemini-3.7-flash"
    ]);
    expect(hostedCleanupTuning("openai", "gpt-5.4-mini")).toEqual({ reasoningEffort: "medium", thinkingLevel: null });
    expect(hostedCleanupTuning("openai", "gpt-5.6-luna")).toEqual({ reasoningEffort: "low", thinkingLevel: null });
    expect(hostedCleanupTuning("gemini", "gemini-3.6-flash")).toEqual({ reasoningEffort: null, thinkingLevel: "minimal" });
    expect(hostedCleanupTuning("gemini", "gemini-3.7-flash")).toEqual({ reasoningEffort: null, thinkingLevel: "low" });
    expect(hostedCleanupTuning("gemini", "gemini-3.5-flash")).toBeNull();
  });

  it("makes 3.7 the first Gemini transcription option and keeps 3.6 cleanup-only", () => {
    const transcriptionModels = hostedOptions("transcription").map(({ model }) => model);
    const cleanupModels = hostedOptions("cleanup").map(({ model }) => model);

    expect(transcriptionModels.filter((model) => model.startsWith("gemini"))[0]).toBe("gemini-3.7-flash");
    expect(transcriptionModels).toContain("gemini-3.5-flash");
    expect(transcriptionModels).not.toContain("gemini-3.6-flash");
    expect(cleanupModels).toContain("gemini-3.6-flash");
    expect(cleanupModels).toContain("gemini-3.7-flash");
    expect(cleanupModels).not.toContain("gemini-3.5-flash");
  });

  it("offers only GPT Transcribe for OpenAI transcription", () => {
    expect(
      hostedOptions("transcription")
        .filter(({ provider }) => provider === "openai")
        .map(({ model }) => model)
    ).toEqual(["gpt-transcribe"]);
  });

  it("reads only supported keys from an env file", () => {
    const file = path.join(os.tmpdir(), `subtitler-env-${Date.now()}.txt`);
    files.push(file);
    fs.writeFileSync(file, "OPENAI_API_KEY='openai-secret'\nGEMINI_API_KEY=gemini-secret\nOTHER=value\n");
    expect(readEnvValues(file)).toEqual({
      OPENAI_API_KEY: "openai-secret",
      GEMINI_API_KEY: "gemini-secret"
    });
  });

  it("recommends a distinct Gemini fallback and no distinct OpenAI fallback", () => {
    expect(recommendedFallbackTranscription("gemini", "gemini-3.7-flash")).toEqual({
      provider: "gemini",
      model: "gemini-3.5-flash"
    });
    expect(recommendedFallbackTranscription("gemini", "gemini-3.5-flash")).toEqual({
      provider: "gemini",
      model: "gemini-3.7-flash"
    });
    expect(recommendedFallbackTranscription("openai", "gpt-transcribe")).toEqual({
      provider: "openai",
      model: "gpt-transcribe"
    });
  });

  it("verifies the supported OpenAI and Gemini models", async () => {
    const file = path.join(os.tmpdir(), `subtitler-env-${Date.now()}.txt`);
    files.push(file);
    fs.writeFileSync(file, "OPENAI_API_KEY=openai-secret\nGEMINI_API_KEY=gemini-secret\n");
    globalThis.fetch = (async (url: string | URL | Request) => {
      const text = String(url);
      if (text.includes("api.openai.com")) {
        return new Response(JSON.stringify({
          data: [
            { id: "gpt-transcribe" },
            { id: "gpt-5.4-mini" },
            { id: "gpt-5.5" }
            ,{ id: "gpt-5.6-sol" }
            ,{ id: "gpt-5.6-terra" }
            ,{ id: "gpt-5.6-luna" }
          ]
        }), { status: 200 });
      }
      return new Response(JSON.stringify({
        models: [
          { name: "models/gemini-3.5-flash", supportedGenerationMethods: ["generateContent"] },
          { name: "models/gemini-3.7-flash", supportedGenerationMethods: ["generateContent"] },
          { name: "models/gemini-3.6-flash", supportedGenerationMethods: ["generateContent"] },
          { name: "models/gemini-3.1-pro-preview", supportedGenerationMethods: ["generateContent"] },
          { name: "models/gemini-3.1-flash-lite", supportedGenerationMethods: ["generateContent"] }
        ]
      }), { status: 200 });
    }) as typeof fetch;

    const result = await verifyHostedModels(file);

    expect(result.openai.transcriptionGpt).toBe(true);
    expect(result.gemini.transcription).toBe(true);
    expect(result.gemini.transcription37).toBe(true);
    expect(result.gemini.cleanup).toBe(true);
    expect(result.gemini.cleanup37).toBe(true);
  });
});
