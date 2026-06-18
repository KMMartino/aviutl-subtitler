import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { APPROVED_MODELS, readEnvValues } from "./hostedModels";

const files: string[] = [];

afterEach(() => {
  for (const file of files.splice(0)) fs.rmSync(file, { force: true });
});

describe("hosted model verification helpers", () => {
  it("uses the intentionally restricted model set", () => {
    expect(APPROVED_MODELS).toEqual({
      openaiTranscription: "gpt-4o-transcribe",
      openaiTranscriptionMini: "gpt-4o-mini-transcribe",
      openaiCleanup: "gpt-5.4-mini",
      openaiCleanup55: "gpt-5.5",
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
});
