import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { readEnvValues, verifyHostedModels } from "./hostedModels";
import { recommendedFallbackTranscription } from "../shared/hostedModelCatalog";

const files: string[] = [];

afterEach(() => {
  for (const file of files.splice(0)) fs.rmSync(file, { force: true });
  globalThis.fetch = originalFetch;
});

const originalFetch = globalThis.fetch;

describe("hosted model verification helpers", () => {
  it("verifies Alibaba credentials with an empty optional base URL", async () => {
    const file = path.join(os.tmpdir(), `subtitler-qwen-env-${Date.now()}.txt`);
    files.push(file);
    fs.writeFileSync(file, "DASHSCOPE_API_KEY=test-key\nDASHSCOPE_BASE_URL=\n");
    globalThis.fetch = (async (url: string | URL | Request) => {
      expect(String(url)).toBe("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models");
      return new Response(JSON.stringify({ data: [{ id: "qwen-audio-3.1-asr-flash" }, { id: "qwen3.7-flash" }] }), { status: 200 });
    }) as typeof fetch;
    const result = await verifyHostedModels(file);
    expect(result.dashscope).toMatchObject({ keyPresent: true, transcription: true, cleanup: true, error: "" });
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
    expect(recommendedFallbackTranscription("gemini", "gemini-3.8-flash")).toEqual({
      provider: "gemini",
      model: "gemini-3.7-flash"
    });
    expect(recommendedFallbackTranscription("gemini", "gemini-3.7-flash")).toEqual({
      provider: "gemini",
      model: "gemini-3.8-flash"
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
          { name: "models/gemini-3.8-flash", supportedGenerationMethods: ["generateContent"] },
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
    expect(result.gemini.transcription38).toBe(true);
    expect(result.gemini.transcription37).toBe(true);
    expect(result.gemini.cleanup).toBe(true);
    expect(result.gemini.cleanup37).toBe(true);
  });
});
