import { isLocalWorkflow, supportsWorkflowFeature } from "../../shared/workflowCatalog";
import type { CoreWorkflowSettings, WorkflowConfig, WorkflowName } from "./types";
import { hostedCleanupTuning, recommendedFallbackTranscription } from "../../shared/hostedModelCatalog";

export function extractCoreSettings(config: WorkflowConfig): CoreWorkflowSettings {
  const transcriptionProvider = config.backend?.transcriber === "dashscope" ? "dashscope" : config.backend?.transcriber === "openai" ? "openai" : "gemini";
  const transcriptionModel = String(config.backend?.transcription_model ?? "");
  const recommendedFallback = recommendedFallbackTranscription(transcriptionProvider, transcriptionModel);
  const fallbackTranscriptionProvider = config.backend?.fallback_transcriber === "dashscope" || config.backend?.fallback_transcriber === "openai" || config.backend?.fallback_transcriber === "gemini"
    ? config.backend.fallback_transcriber
    : recommendedFallback.provider;
  return {
    audioTrack: Number(config.audio?.track ?? 1),
    editorial: {
      cuttingMode: "voice_gaps",
      gapEdgeMode: "acoustic",
      recommendationsEnabled: config.editorial?.recommendations_enabled !== false,
      voiceGapMinMs: Number(config.editorial?.voice_gap_min_ms ?? 2000),
      voiceLeadingHandleMs: Number(config.editorial?.voice_leading_handle_ms ?? 50),
      voiceTrailingHandleMs: Number(config.editorial?.voice_trailing_handle_ms ?? 100),
      gameAudioTrack: typeof config.editorial?.game_audio_track === "number" ? config.editorial.game_audio_track : undefined
    },
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
      automatic: config.backend?.auto_select_hosted_models === true,
      transcriptionProvider,
      transcriptionModel,
      fallbackTranscriptionProvider,
      fallbackTranscriptionModel: String(config.backend?.fallback_transcription_model ?? recommendedFallback.model),
      cleanupProvider: config.cleanup?.backend === "dashscope" ? "dashscope" : config.cleanup?.backend === "gemini" ? "gemini" : "openai",
      cleanupModel: String(config.cleanup?.api_model ?? ""),
      envFile: ""
    },
    diagnostics: {
      profile: Boolean(config.diagnostics?.profile)
    },
    cost: {
      estimateCostOnly: Boolean(config.cost?.estimate_cost_only)
    },
    additionalSettings: {
      youtubeChapters: Boolean(config.additional_settings?.youtube_chapters),
      cutSilenceMode: ["automatic", "review"].includes(String(config.additional_settings?.cut_silence_mode))
        ? config.additional_settings?.cut_silence_mode as "automatic" | "review"
        : "off",
      renderCutVideo: Boolean(config.additional_settings?.render_cut_video),
      brollMode: ["automatic", "review"].includes(String(config.additional_settings?.broll_mode))
        ? "automatic"
        : "off"
    },
    cleanupGroupPolicy: {
      minSec: Number(config.cleanup?.group_min_sec ?? 60),
      durationDivisor: Number(config.cleanup?.group_duration_divisor ?? 2),
      maxSec: Number(config.cleanup?.group_max_sec ?? 600)
    },
    alignment: {
      model: String(config.alignment?.model ?? "MahmoudAshraf/mms-300m-1130-forced-aligner"),
      offlineModelCache: Boolean(config.alignment?.offline_model_cache)
    }
  };
}

export function applyCoreSettings(config: WorkflowConfig, settings: CoreWorkflowSettings, workflow: WorkflowName): WorkflowConfig {
  const next = structuredClone(config);
  const localWorkflow = isLocalWorkflow(workflow);
  next.audio ??= {};
  next.backend ??= {};
  next.cleanup ??= {};
  next.diagnostics ??= {};
  next.cost ??= {};
  next.additional_settings ??= {};
  next.workflow ??= {};
  next.alignment ??= {};
  next.audio.track = settings.audioTrack;
  if (workflow === "hosted-long-stream") {
    next.editorial ??= {};
    next.editorial.cutting_mode = "voice_gaps";
    next.editorial.gap_edge_mode = "acoustic";
    next.editorial.recommendations_enabled = settings.editorial?.recommendationsEnabled ?? true;
    next.editorial.voice_gap_min_ms = settings.editorial?.voiceGapMinMs ?? 2000;
    next.editorial.voice_leading_handle_ms = settings.editorial?.voiceLeadingHandleMs ?? 50;
    next.editorial.voice_trailing_handle_ms = settings.editorial?.voiceTrailingHandleMs ?? 100;
    next.editorial.game_audio_track = settings.editorial?.gameAudioTrack ?? null;
  }
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
    next.backend.auto_select_hosted_models = settings.hosted?.automatic ?? false;
    next.backend.transcription_model = settings.hosted?.transcriptionModel ?? next.backend.transcription_model ?? "";
    next.backend.transcriber = settings.hosted?.transcriptionProvider ?? next.backend.transcriber ?? "gemini";
    next.backend.fallback_transcription_model = settings.hosted?.fallbackTranscriptionModel ?? next.backend.fallback_transcription_model ?? "";
    next.backend.fallback_transcriber = settings.hosted?.fallbackTranscriptionProvider ?? next.backend.fallback_transcriber ?? "openai";
    next.cleanup.api_model = settings.hosted?.cleanupModel ?? next.cleanup.api_model ?? "";
    next.cleanup.backend = settings.hosted?.cleanupProvider ?? next.cleanup.backend ?? "openai";
    const tuning = hostedCleanupTuning(next.cleanup.backend as "openai" | "gemini" | "dashscope", String(next.cleanup.api_model));
    next.cleanup.reasoning_effort = tuning?.reasoningEffort ?? null;
    next.cleanup.thinking_level = tuning?.thinkingLevel ?? null;
  }
  next.diagnostics.profile = settings.diagnostics.profile;
  delete next.cost.max_estimated_api_cost_usd;
  delete next.cost.allow_api_spend;
  next.cost.estimate_cost_only = settings.cost?.estimateCostOnly ?? false;
  next.additional_settings.youtube_chapters = supportsWorkflowFeature(workflow, "chapters") ? settings.additionalSettings?.youtubeChapters ?? false : false;
  next.additional_settings.cut_silence_mode = supportsWorkflowFeature(workflow, "silence")
    ? settings.additionalSettings?.cutSilenceMode ?? "off"
    : "off";
  next.additional_settings.render_cut_video = supportsWorkflowFeature(workflow, "silence")
    ? settings.additionalSettings?.renderCutVideo ?? false
    : false;
  next.additional_settings.broll_mode = supportsWorkflowFeature(workflow, "broll")
    ? settings.additionalSettings?.brollMode ?? "off"
    : "off";
  if (settings.cleanupGroupPolicy !== undefined) {
    next.cleanup.group_min_sec = settings.cleanupGroupPolicy.minSec;
    next.cleanup.group_duration_divisor = settings.cleanupGroupPolicy.durationDivisor;
    next.cleanup.group_max_sec = settings.cleanupGroupPolicy.maxSec;
  }
  next.alignment.model = settings.alignment?.model ?? next.alignment.model;
  next.alignment.offline_model_cache = settings.alignment?.offlineModelCache ?? false;
  return next;
}

export function applySharedAlignment(config: WorkflowConfig, model: string, offlineModelCache: boolean): WorkflowConfig {
  return { ...config, alignment: { ...(config.alignment ?? {}), model, offline_model_cache: offlineModelCache } };
}
