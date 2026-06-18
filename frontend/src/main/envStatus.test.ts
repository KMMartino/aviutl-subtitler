import { describe, expect, it } from "vitest";
import { parseEnvKeys } from "./envStatus";

describe("env status", () => {
  it("reports key presence without values", () => {
    const keys = parseEnvKeys("OPENAI_API_KEY=secret\nGEMINI_API_KEY='also-secret'\nIGNORED=value\n");

    expect(keys.has("OPENAI_API_KEY")).toBe(true);
    expect(keys.has("GEMINI_API_KEY")).toBe(true);
    expect(keys.has("secret")).toBe(false);
  });
});
