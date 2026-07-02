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
      cost: { max_estimated_api_cost_usd: 5, allow_api_spend: false, estimate_cost_only: false },
      additional_settings: { youtube_chapters: true }
    };
    const core = extractCoreSettings(config);
    expect(core.additionalSettings?.youtubeChapters).toBe(true);
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
    expect(next.additional_settings.youtube_chapters).toBe(false);
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

  it("applies YouTube chapters only for hosted short workflow", () => {
    const core = {
      audioTrack: 1,
      hosted: {
        transcriptionProvider: "gemini" as const,
        transcriptionModel: "gemini-3.5-flash",
        fallbackTranscriptionProvider: "openai" as const,
        fallbackTranscriptionModel: "gpt-4o-mini-transcribe",
        cleanupProvider: "openai" as const,
        cleanupModel: "gpt-5.4-mini",
        envFile: ""
      },
      diagnostics: { profile: true },
      additionalSettings: { youtubeChapters: true }
    };

    expect(applyCoreSettings({}, core, "hosted").additional_settings.youtube_chapters).toBe(true);
    expect(applyCoreSettings({}, core, "hosted-long-stream").additional_settings.youtube_chapters).toBe(false);
    expect(applyCoreSettings({}, core, "local").additional_settings.youtube_chapters).toBe(false);
  });

  it("round-trips hosted fallback transcription settings", () => {
    const core = extractCoreSettings({
      audio: { track: 1 },
      backend: {
        transcriber: "gemini",
        transcription_model: "gemini-3.5-flash",
        fallback_transcriber: "openai",
        fallback_transcription_model: "gpt-4o-mini-transcribe"
      },
      cleanup: { backend: "openai", api_model: "gpt-5.4-mini" },
      diagnostics: { profile: true }
    });

    expect(core.hosted?.fallbackTranscriptionProvider).toBe("openai");
    expect(core.hosted?.fallbackTranscriptionModel).toBe("gpt-4o-mini-transcribe");

    const next = applyCoreSettings({}, core, "hosted");
    expect(next.backend.fallback_transcriber).toBe("openai");
    expect(next.backend.fallback_transcription_model).toBe("gpt-4o-mini-transcribe");
  });
});
