import { describe, expect, it } from "vitest";
import { applyCoreSettings, applySharedAlignment, extractCoreSettings } from "./configPatch";

describe("config patching", () => {
  it("shares alignment selection without discarding advanced alignment options", () => {
    expect(applySharedAlignment({ alignment: { language: "ja", split_size: "char", model: "old" } }, "managed", true).alignment).toEqual({ language: "ja", split_size: "char", model: "managed", offline_model_cache: true });
  });
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

    expect(next.audio!.track).toBe(0);
    expect(next.backend!.model).toBe("new.gguf");
    expect(next.diagnostics!.profile).toBe(false);
    expect((next.vad as Record<string, unknown>).max_chunk_sec).toBe(30);
    expect(next.additional_settings!.youtube_chapters).toBe(false);
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
    expect(next.backend!.transcriber).toBe("local-gemma");
    expect(next.cleanup!.backend).toBe("local-llama");
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

    expect(applyCoreSettings({}, core, "hosted").additional_settings!.youtube_chapters).toBe(true);
    expect(applyCoreSettings({}, core, "hosted-long-stream").additional_settings!.youtube_chapters).toBe(false);
    expect(applyCoreSettings({}, core, "local").additional_settings!.youtube_chapters).toBe(false);
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
    expect(next.backend!.fallback_transcriber).toBe("openai");
    expect(next.backend!.fallback_transcription_model).toBe("gpt-4o-mini-transcribe");
  });

  it("applies Cut silence only to short workflows", () => {
    const core = extractCoreSettings({ additional_settings: { cut_silence_mode: "review", render_cut_video: true } });
    expect(core.additionalSettings?.cutSilenceMode).toBe("review");
    expect(core.additionalSettings?.renderCutVideo).toBe(true);
    expect(applyCoreSettings({}, core, "local").additional_settings?.cut_silence_mode).toBe("review");
    expect(applyCoreSettings({}, core, "hosted").additional_settings?.cut_silence_mode).toBe("review");
    expect(applyCoreSettings({}, core, "local-long-stream").additional_settings?.cut_silence_mode).toBe("off");
    expect(applyCoreSettings({}, core, "local").additional_settings?.render_cut_video).toBe(true);
    expect(applyCoreSettings({}, core, "local-long-stream").additional_settings?.render_cut_video).toBe(false);
  });

  it("defaults hosted fallback transcription to the recommended model pair", () => {
    const core = extractCoreSettings({
      audio: { track: 1 },
      backend: {
        transcriber: "gemini",
        transcription_model: "gemini-3.5-flash"
      },
      cleanup: { backend: "openai", api_model: "gpt-5.4-mini" },
      diagnostics: { profile: true }
    });

    expect(core.hosted?.fallbackTranscriptionProvider).toBe("gemini");
    expect(core.hosted?.fallbackTranscriptionModel).toBe("gemini-3.1-pro-preview");
  });

  it.each([
    ["openai", "gpt-5.4-mini", "medium", null],
    ["openai", "gpt-5.6-luna", "low", null],
    ["gemini", "gemini-3.5-flash", null, "minimal"],
  ] as const)("pins the tested cleanup tuning for %s:%s", (provider, model, reasoning, thinking) => {
    const next = applyCoreSettings({}, {
      audioTrack: 1,
      hosted: {
        transcriptionProvider: "gemini",
        transcriptionModel: "gemini-3.5-flash",
        fallbackTranscriptionProvider: "gemini",
        fallbackTranscriptionModel: "gemini-3.1-pro-preview",
        cleanupProvider: provider,
        cleanupModel: model,
        envFile: ""
      },
      diagnostics: { profile: true }
    }, "hosted");

    expect(next.cleanup?.reasoning_effort).toBe(reasoning);
    expect(next.cleanup?.thinking_level).toBe(thinking);
  });
});
