import type { CoreWorkflowSettings, WorkflowConfig, WorkflowName } from "./types";

export function extractCoreSettings(config: WorkflowConfig): CoreWorkflowSettings {
  return {
    audioTrack: Number(config.audio?.track ?? 1),
    local: {
      model: String(config.backend?.model ?? ""),
      mmproj: String(config.backend?.mmproj ?? ""),
      llamaServer: String(config.backend?.llama_server ?? ""),
      cleanupModel: String(config.cleanup?.model ?? ""),
      cleanupLlamaServer: String(config.cleanup?.llama_server ?? ""),
      transcriptionDraftModel: String(config.backend?.spec_draft_model ?? ""),
      cleanupDraftModel: String(config.cleanup?.spec_draft_model ?? "")
    },
    hosted: {
      transcriptionProvider: config.backend?.transcriber === "openai" ? "openai" : "gemini",
      transcriptionModel: String(config.backend?.transcription_model ?? ""),
      fallbackTranscriptionProvider: config.backend?.fallback_transcriber === "gemini" ? "gemini" : "openai",
      fallbackTranscriptionModel: String(config.backend?.fallback_transcription_model ?? "gpt-4o-mini-transcribe"),
      cleanupProvider: config.cleanup?.backend === "gemini" ? "gemini" : "openai",
      cleanupModel: String(config.cleanup?.api_model ?? ""),
      envFile: ""
    },
    diagnostics: {
      profile: Boolean(config.diagnostics?.profile)
    },
    cost: {
      maxEstimatedApiCostUsd: Number(config.cost?.max_estimated_api_cost_usd ?? 5),
      allowApiSpend: Boolean(config.cost?.allow_api_spend),
      estimateCostOnly: Boolean(config.cost?.estimate_cost_only)
    },
    additionalSettings: {
      youtubeChapters: Boolean(config.additional_settings?.youtube_chapters)
    },
    cleanupWindowSubtitles: Number(config.cleanup?.window_subtitles ?? 0) || undefined
  };
}

export function applyCoreSettings(config: WorkflowConfig, settings: CoreWorkflowSettings, workflow: WorkflowName): WorkflowConfig {
  const next = structuredClone(config);
  const localWorkflow = workflow === "local" || workflow === "local-long-stream";
  next.audio ??= {};
  next.backend ??= {};
  next.cleanup ??= {};
  next.diagnostics ??= {};
  next.cost ??= {};
  next.additional_settings ??= {};
  next.audio.track = settings.audioTrack;
  if (localWorkflow) {
    next.backend.transcriber = "local-gemma";
    next.cleanup.backend = "local-llama";
    next.backend.model = settings.local?.model ?? next.backend.model ?? "";
    next.backend.mmproj = settings.local?.mmproj ?? next.backend.mmproj ?? "";
    next.backend.llama_server = settings.local?.llamaServer ?? next.backend.llama_server ?? "";
    next.cleanup.model = settings.local?.cleanupModel ?? next.cleanup.model ?? "";
    next.cleanup.llama_server = settings.local?.cleanupLlamaServer ?? next.cleanup.llama_server ?? "";
    next.backend.spec_draft_model = settings.local?.transcriptionDraftModel ?? "";
    next.cleanup.spec_draft_model = settings.local?.cleanupDraftModel ?? "";
    next.backend.transcription_model = "";
    next.backend.fallback_transcriber = "";
    next.backend.fallback_transcription_model = "";
    next.cleanup.api_model = "";
  } else {
    next.backend.transcription_model = settings.hosted?.transcriptionModel ?? next.backend.transcription_model ?? "";
    next.backend.transcriber = settings.hosted?.transcriptionProvider ?? next.backend.transcriber ?? "gemini";
    next.backend.fallback_transcription_model = settings.hosted?.fallbackTranscriptionModel ?? next.backend.fallback_transcription_model ?? "";
    next.backend.fallback_transcriber = settings.hosted?.fallbackTranscriptionProvider ?? next.backend.fallback_transcriber ?? "openai";
    next.cleanup.api_model = settings.hosted?.cleanupModel ?? next.cleanup.api_model ?? "";
    next.cleanup.backend = settings.hosted?.cleanupProvider ?? next.cleanup.backend ?? "openai";
  }
  next.diagnostics.profile = settings.diagnostics.profile;
  next.cost.max_estimated_api_cost_usd = settings.cost?.maxEstimatedApiCostUsd ?? next.cost.max_estimated_api_cost_usd ?? 5;
  next.cost.allow_api_spend = settings.cost?.allowApiSpend ?? false;
  next.cost.estimate_cost_only = settings.cost?.estimateCostOnly ?? false;
  next.additional_settings.youtube_chapters = workflow === "hosted" ? settings.additionalSettings?.youtubeChapters ?? false : false;
  if (settings.cleanupWindowSubtitles !== undefined) {
    next.cleanup.window_subtitles = settings.cleanupWindowSubtitles;
  }
  return next;
}
