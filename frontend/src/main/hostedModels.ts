import fs from "node:fs";
import type { HostedModelVerification } from "../renderer/lib/types";
import { APPROVED_MODELS, OPENAI_TRANSCRIPTION_MODEL_ALIASES } from "../shared/hostedModelCatalog";

export { APPROVED_MODELS };

export async function verifyHostedModels(envFile: string): Promise<HostedModelVerification> {
  const keys = readEnvValues(envFile);
  const [openai, gemini] = await Promise.all([
    verifyOpenAI(keys.OPENAI_API_KEY),
    verifyGemini(keys.GEMINI_API_KEY)
  ]);
  return { checkedAt: new Date().toISOString(), openai, gemini };
}

async function verifyOpenAI(apiKey = ""): Promise<HostedModelVerification["openai"]> {
  if (!apiKey) return { keyPresent: false, error: "", transcription: false, transcriptionMini: false, cleanup: false, cleanup55: false };
  try {
    const response = await fetch("https://api.openai.com/v1/models", {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: AbortSignal.timeout(30000)
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const body = await response.json() as { data?: Array<{ id?: string }> };
    const names = new Set((body.data ?? []).map((model) => String(model.id ?? "")));
    const hasAlias = (model: string) => (OPENAI_TRANSCRIPTION_MODEL_ALIASES[model] ?? [model]).some((alias) => names.has(alias));
    return {
      keyPresent: true,
      error: "",
      transcription: hasAlias(APPROVED_MODELS.openaiTranscription),
      transcriptionMini: hasAlias(APPROVED_MODELS.openaiTranscriptionMini),
      cleanup: names.has(APPROVED_MODELS.openaiCleanup),
      cleanup55: names.has(APPROVED_MODELS.openaiCleanup55)
    };
  } catch (error) {
    return { keyPresent: true, error: errorMessage(error), transcription: false, transcriptionMini: false, cleanup: false, cleanup55: false };
  }
}

async function verifyGemini(apiKey = ""): Promise<HostedModelVerification["gemini"]> {
  if (!apiKey) return { keyPresent: false, error: "", transcription: false, transcription31Pro: false, transcription31FlashLite: false, cleanup: false, cleanup31Pro: false, cleanup31FlashLite: false };
  try {
    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(apiKey)}&pageSize=1000`, {
      signal: AbortSignal.timeout(30000)
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const body = await response.json() as { models?: Array<{ name?: string; supportedGenerationMethods?: string[] }> };
    const supports = (name: string) => Boolean((body.models ?? []).find(
      (item) => String(item.name ?? "").replace(/^models\//, "") === name
    )?.supportedGenerationMethods?.includes("generateContent"));
    const gemini35 = supports(APPROVED_MODELS.gemini);
    return {
      keyPresent: true,
      error: "",
      transcription: gemini35,
      transcription31Pro: supports(APPROVED_MODELS.gemini31Pro),
      transcription31FlashLite: supports(APPROVED_MODELS.gemini31FlashLite),
      cleanup: gemini35,
      cleanup31Pro: supports(APPROVED_MODELS.gemini31Pro),
      cleanup31FlashLite: supports(APPROVED_MODELS.gemini31FlashLite)
    };
  } catch (error) {
    return { keyPresent: true, error: errorMessage(error), transcription: false, transcription31Pro: false, transcription31FlashLite: false, cleanup: false, cleanup31Pro: false, cleanup31FlashLite: false };
  }
}

export function readEnvValues(envFile: string): Record<string, string> {
  if (!envFile || !fs.existsSync(envFile)) return {};
  const values: Record<string, string> = {};
  for (const rawLine of fs.readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const [rawKey, ...rest] = line.split("=");
    const key = rawKey.trim();
    if (key !== "OPENAI_API_KEY" && key !== "GEMINI_API_KEY") continue;
    values[key] = rest.join("=").trim().replace(/^(['"])(.*)\1$/, "$2");
  }
  return values;
}

async function responseMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as { error?: { message?: string } };
    return parsed.error?.message || `HTTP ${response.status}`;
  } catch {
    return text.trim() || `HTTP ${response.status}`;
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
