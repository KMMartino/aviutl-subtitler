import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { APPROVED_MODELS, readEnvValues, verifyHostedModels } from "./hostedModels";
import { recommendedFallbackTranscription } from "../shared/hostedModelCatalog";

const files: string[] = [];

afterEach(() => {
  for (const file of files.splice(0)) fs.rmSync(file, { force: true });
  globalThis.fetch = originalFetch;
});

const originalFetch = globalThis.fetch;

describe("hosted model verification helpers", () => {
  it("uses the intentionally restricted model set", () => {
    expect(APPROVED_MODELS).toEqual({
      openaiTranscription: "gpt-4o-transcribe",
      openaiTranscriptionMini: "gpt-4o-mini-transcribe",
      openaiCleanup: "gpt-5.4-mini",
      openaiCleanup55: "gpt-5.5",
      openaiCleanup56Sol: "gpt-5.6-sol",
      openaiCleanup56Terra: "gpt-5.6-terra",
      openaiCleanup56Luna: "gpt-5.6-luna",
      gemini: "gemini-3.5-flash",
      gemini31Pro: "gemini-3.1-pro-preview",
      gemini31FlashLite: "gemini-3.1-flash-lite"
    });
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

  it("recommends a smarter fallback unless the primary is already the smartest", () => {
    expect(recommendedFallbackTranscription("gemini", "gemini-3.5-flash")).toEqual({
      provider: "gemini",
      model: "gemini-3.1-pro-preview"
    });
    expect(recommendedFallbackTranscription("gemini", "gemini-3.1-pro-preview")).toEqual({
      provider: "gemini",
      model: "gemini-3.5-flash"
    });
    expect(recommendedFallbackTranscription("openai", "gpt-4o-mini-transcribe")).toEqual({
      provider: "openai",
      model: "gpt-4o-transcribe"
    });
  });

  it("treats OpenAI dated transcription aliases as the canonical mini model", async () => {
    const file = path.join(os.tmpdir(), `subtitler-env-${Date.now()}.txt`);
    files.push(file);
    fs.writeFileSync(file, "OPENAI_API_KEY=openai-secret\nGEMINI_API_KEY=gemini-secret\n");
    globalThis.fetch = (async (url: string | URL | Request) => {
      const text = String(url);
      if (text.includes("api.openai.com")) {
        return new Response(JSON.stringify({
          data: [
            { id: "gpt-4o-mini-transcribe-2025-12-15" },
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
          { name: "models/gemini-3.1-pro-preview", supportedGenerationMethods: ["generateContent"] },
          { name: "models/gemini-3.1-flash-lite", supportedGenerationMethods: ["generateContent"] }
        ]
      }), { status: 200 });
    }) as typeof fetch;

    const result = await verifyHostedModels(file);

    expect(result.openai.transcriptionMini).toBe(true);
  });
});
