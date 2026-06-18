import fs from "node:fs";
import type { EnvStatus } from "../renderer/lib/types";

const trackedKeys = ["OPENAI_API_KEY", "GEMINI_API_KEY"] as const;

export function getEnvStatus(envFile: string): EnvStatus {
  if (!envFile || !fs.existsSync(envFile)) {
    return { exists: false, keysPresent: { OPENAI_API_KEY: false, GEMINI_API_KEY: false } };
  }
  const parsed = parseEnvKeys(fs.readFileSync(envFile, "utf8"));
  return {
    exists: true,
    keysPresent: {
      OPENAI_API_KEY: parsed.has("OPENAI_API_KEY"),
      GEMINI_API_KEY: parsed.has("GEMINI_API_KEY")
    }
  };
}

export function parseEnvKeys(text: string): Set<string> {
  const keys = new Set<string>();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const [rawKey, ...rest] = line.split("=");
    const key = rawKey.trim();
    const value = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
    if (trackedKeys.includes(key as (typeof trackedKeys)[number]) && value) {
      keys.add(key);
    }
  }
  return keys;
}
