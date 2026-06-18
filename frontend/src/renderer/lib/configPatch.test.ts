import { describe, expect, it } from "vitest";
import { applyCoreSettings, extractCoreSettings } from "./configPatch";

describe("config patching", () => {
  it("extracts and applies core settings without touching advanced sections", () => {
    const config = {
      audio: { track: 1 },
      backend: { model: "old.gguf", mmproj: "old-mmproj.gguf", llama_server: "server.exe", transcription_model: "gemini" },
      cleanup: { model: "cleanup.gguf", llama_server: "server.exe", api_model: "gpt" },
      diagnostics: { profile: true },
      vad: { max_chunk_sec: 30 },
      cost: { max_estimated_api_cost_usd: 5, allow_api_spend: false, estimate_cost_only: false }
    };
    const core = extractCoreSettings(config);
    const next = applyCoreSettings(config, {
      ...core,
      audioTrack: 0,
      local: { ...core.local!, model: "new.gguf" },
      diagnostics: { profile: false }
    }, "local");

    expect(next.audio.track).toBe(0);
    expect(next.backend.model).toBe("new.gguf");
    expect(next.diagnostics.profile).toBe(false);
    expect(next.vad.max_chunk_sec).toBe(30);
  });

  it("repairs local workflow provider fields", () => {
    const next = applyCoreSettings(
      { backend: { transcriber: "gemini" }, cleanup: { backend: "openai" }, audio: {}, diagnostics: {} },
      {
        audioTrack: 1,
        local: {
          model: "model.gguf",
          mmproj: "mmproj.gguf",
          llamaServer: "server.exe",
          cleanupModel: "cleanup.gguf",
          cleanupLlamaServer: "server.exe",
          transcriptionDraftModel: "",
          cleanupDraftModel: ""
        },
        diagnostics: { profile: true }
      },
      "local"
    );
    expect(next.backend.transcriber).toBe("local-gemma");
    expect(next.cleanup.backend).toBe("local-llama");
  });
});
