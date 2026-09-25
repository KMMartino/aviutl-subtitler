import fs from "node:fs";
import type { HostedModelVerification } from "../renderer/lib/types";
import { APPROVED_MODELS } from "../shared/hostedModelCatalog";

export { APPROVED_MODELS };

export async function verifyHostedModels(envFile: string): Promise<HostedModelVerification> {
  const keys = readEnvValues(envFile);
  const [openai, gemini, dashscope] = await Promise.all([
    verifyOpenAI(keys.OPENAI_API_KEY),
    verifyGemini(keys.GEMINI_API_KEY),
    verifyQwen(keys.DASHSCOPE_API_KEY, keys.DASHSCOPE_BASE_URL)
  ]);
  return { checkedAt: new Date().toISOString(), openai, gemini, dashscope };
}

async function verifyOpenAI(apiKey = ""): Promise<HostedModelVerification["openai"]> {
  if (!apiKey) return { keyPresent: false, error: "", transcriptionGpt: false, cleanup: false, cleanup56Luna: false, cleanup6Luna: false };
  try {
    const response = await fetch("https://api.openai.com/v1/models", {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: AbortSignal.timeout(30000)
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const body = await response.json() as { data?: Array<{ id?: string }> };
    const names = new Set((body.data ?? []).map((model) => String(model.id ?? "")));
    return {
      keyPresent: true,
      error: "",
      transcriptionGpt: names.has(APPROVED_MODELS.openaiTranscriptionGpt),
      cleanup: names.has(APPROVED_MODELS.openaiCleanup),
      cleanup56Luna: names.has(APPROVED_MODELS.openaiCleanup56Luna),
      cleanup6Luna: names.has(APPROVED_MODELS.openaiCleanup6Luna)
    };
  } catch (error) {
    return { keyPresent: true, error: errorMessage(error), transcriptionGpt: false, cleanup: false, cleanup56Luna: false, cleanup6Luna: false };
  }
}

async function verifyGemini(apiKey = ""): Promise<HostedModelVerification["gemini"]> {
  if (!apiKey) return { keyPresent: false, error: "", transcription: false, transcription38: false, transcription37: false, transcription31Pro: false, transcription31FlashLite: false, cleanup: false, cleanup37: false };
  try {
    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(apiKey)}&pageSize=1000`, {
      signal: AbortSignal.timeout(30000)
    });
    if (!response.ok) throw new Error(await responseMessage(response));
    const body = await response.json() as { models?: Array<{ name?: string; supportedGenerationMethods?: string[] }> };
    const supports = (name: string) => Boolean((body.models ?? []).find(
      (item) => String(item.name ?? "").replace(/^models\//, "") === name
    )?.supportedGenerationMethods?.includes("generateContent"));
    return {
      keyPresent: true,
      error: "",
      transcription: supports(APPROVED_MODELS.gemini),
      transcription38: supports(APPROVED_MODELS.gemini38Flash),
      transcription37: supports(APPROVED_MODELS.gemini37Flash),
      transcription31Pro: supports(APPROVED_MODELS.gemini31Pro),
      transcription31FlashLite: supports(APPROVED_MODELS.gemini31FlashLite),
      cleanup: supports(APPROVED_MODELS.gemini36Flash),
      cleanup37: supports(APPROVED_MODELS.gemini37Flash)
    };
  } catch (error) {
    return { keyPresent: true, error: errorMessage(error), transcription: false, transcription38: false, transcription37: false, transcription31Pro: false, transcription31FlashLite: false, cleanup: false, cleanup37: false };
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
    if (!["OPENAI_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"].includes(key)) continue;
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

async function verifyQwen(apiKey = "", base = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"): Promise<NonNullable<HostedModelVerification["dashscope"]>> {
  base = base.trim() || "https://dashscope-intl.aliyuncs.com/compatible-mode/v1";
  const result = { keyPresent: Boolean(apiKey), error: "", transcription: false, cleanup: false };
  if (!apiKey) return result;
  try {
    const url = new URL(base);
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || !url.pathname.replace(/\/$/, "").endsWith("/compatible-mode/v1")) throw new Error("Invalid DASHSCOPE_BASE_URL");
    const response = await fetch(base.replace(/\/$/, "") + "/models", { headers: { Authorization: `Bearer ${apiKey}` }, signal: AbortSignal.timeout(30000) });
    if (!response.ok) throw new Error(await responseMessage(response));
    const body = await response.json() as { data?: Array<{ id?: string }> };
    const names = new Set((body.data ?? []).map((model) => model.id));
    return { ...result, transcription: names.has(APPROVED_MODELS.qwenAsr), cleanup: names.has(APPROVED_MODELS.qwenCleanup) };
  } catch (error) { return { ...result, error: errorMessage(error).replaceAll(apiKey, "[REDACTED]") }; }
}
