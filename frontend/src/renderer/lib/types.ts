export type WorkflowName = "local" | "hosted" | "local-long-stream" | "hosted-long-stream";
export type ThemeName =
  | "paper"
  | "sage"
  | "sky"
  | "rose"
  | "graphite"
  | "forest"
  | "midnight"
  | "plum";

export type AppSettings = {
  schemaVersion: number;
  pythonPath: string;
  envFile: string;
  lastInputPath: string;
  lastOutputDir: string;
  lastSidecarDir: string;
  selectedWorkflow: WorkflowName;
  sidecarsEnabled: boolean;
  theme: ThemeName;
  modelsDirectory: string;
  localModelProfile: string;
  llamaBackend: LlamaBackendId;
  ffmpegMode?: "auto" | "managed" | "path";
  modelDownloadMode?: "direct" | "huggingface";
  alignmentModel: string;
  alignmentOfflineModelCache: boolean;
  cutSilenceEncoderPreset: CutSilenceEncoderPreset;
  silencePreviewHeight: 240 | 360 | 480 | 720;
  silencePreviewFps: 4 | 8 | 12 | 24;
};

export type CutSilenceMode = "off" | "automatic" | "review";
export type CutSilenceEncoderPreset = "unconfigured" | "hevc-amf-cqp21" | "hevc-nvenc-qp21" | "hevc-qsv-q21" | "libx265-crf21";
export type SilenceCutDecision = "accept_cut" | "reject_cut" | "mark_and_reject";
export type MediaFrameRateMode = "reported-cfr" | "possible-vfr" | "unknown";

export type SilenceCutCandidate = {
  id: string;
  silenceStart: number;
  silenceEnd: number;
  cutStart: number;
  cutEnd: number;
  cutDuration: number;
};

export type EncoderProbeResult = {
  preset: Exclude<CutSilenceEncoderPreset, "unconfigured">;
  label: string;
  available: boolean;
  error: string;
};

export type LlamaBackendId = "vulkan" | "cuda-12";

export type LlamaBackendOption = {
  id: LlamaBackendId;
  label: string;
  description: string;
};

export type LlamaReleaseAsset = {
  backend: LlamaBackendId;
  releaseTag: string;
  assetName: string;
  downloadUrl: string;
  bytes: number;
  sha256: string;
};

export type ManagedLlamaStatus = {
  backend: LlamaBackendId;
  releaseTag: string;
  installed: boolean;
  installDir: string;
  serverPath: string;
  version: string;
};

export type CurrentLlamaServerState = {
  managed: boolean;
  valid: boolean;
  backend: LlamaBackendId | "";
  releaseTag: string;
  serverPath: string;
  version: string;
  previous: ManagedLlamaStatus | null;
};

export type LlamaReleaseCheck = {
  releaseTag: string;
  assets: LlamaReleaseAsset[];
  checkedAt: string;
};

export type LocalModelStatus = {
  profile: string;
  installed: boolean;
  needsVerification: boolean;
  downloading: boolean;
  managed: boolean;
  files: {
    transcription: { path: string; exists: boolean };
    projector: { path: string; exists: boolean };
    cleanup: { path: string; exists: boolean };
    transcriptionDraft?: { path: string; exists: boolean };
    cleanupDraft?: { path: string; exists: boolean };
  };
};

export type LocalModelProfile = {
  id: string;
  label: string;
  vramGb: number;
  summary: string;
  downloadBytes: number;
  cleanupGroupPolicy: CleanupGroupPolicy;
  experimental: boolean;
};

export type CleanupGroupPolicy = {
  minSec: number;
  durationDivisor: number;
  maxSec: number;
};

export type HuggingFaceDownloaderStatus = {
  ready: boolean;
  pythonReady: boolean;
  pythonPath: string;
  pythonSource: PythonRuntimeStatus["source"];
  version: string;
  xetReady: boolean;
  error: string;
};

export type EnvStatus = {
  exists: boolean;
  keysPresent: {
    OPENAI_API_KEY: boolean;
    GEMINI_API_KEY: boolean;
  };
};

export type PythonRuntimeStatus = {
  selectedPath: string;
  resolvedPath: string;
  source: "selected" | "managed" | "path" | "missing";
  ready: boolean;
  version: string;
  venvPath: string;
  managedInstalled: boolean;
  requirementsInstalled: boolean;
  error: string;
};

export type FfmpegStatus = {
  source: "path" | "managed" | "missing";
  ffmpegPath: string;
  ffprobePath: string;
  version: string;
  ready: boolean;
  managedInstalled: boolean;
  error: string;
};

export type RuntimeSetupStatus = {
  python: PythonRuntimeStatus;
  ffmpeg: FfmpegStatus;
  alignment: AlignmentModelStatus;
};

export type AlignmentModelStatus = {
  installed: boolean;
  modelPath: string;
  cachePath: string;
  revision: string;
  downloadBytes: number;
  verified: boolean;
  error: string;
};

export type HostedModelVerification = {
  checkedAt: string;
  openai: {
    keyPresent: boolean;
    error: string;
    transcription: boolean;
    transcriptionMini: boolean;
    cleanup: boolean;
    cleanup56Luna: boolean;
  };
  gemini: {
    keyPresent: boolean;
    error: string;
    transcription: boolean;
    transcription31Pro: boolean;
    transcription31FlashLite: boolean;
    cleanup: boolean;
  };
};

export type CoreWorkflowSettings = {
  audioTrack: number;
  local?: {
    model: string;
    mmproj: string;
    llamaServer: string;
    cleanupModel: string;
    cleanupLlamaServer: string;
    transcriptionDraftModel: string;
    cleanupDraftModel: string;
  };
  hosted?: {
    transcriptionProvider: "openai" | "gemini";
    transcriptionModel: string;
    fallbackTranscriptionProvider: "openai" | "gemini";
    fallbackTranscriptionModel: string;
    cleanupProvider: "openai" | "gemini";
    cleanupModel: string;
    envFile: string;
  };
  diagnostics: {
    profile: boolean;
  };
  cost?: {
    maxEstimatedApiCostUsd: number;
    allowApiSpend: boolean;
    estimateCostOnly: boolean;
  };
  additionalSettings?: {
    youtubeChapters: boolean;
    cutSilenceMode?: CutSilenceMode;
    renderCutVideo?: boolean;
  };
  cleanupGroupPolicy?: CleanupGroupPolicy;
  alignment?: {
    model: string;
    offlineModelCache: boolean;
  };
};

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type WorkflowConfigSection = Record<string, JsonValue | undefined>;
export type WorkflowConfig = {
  audio?: WorkflowConfigSection;
  backend?: WorkflowConfigSection;
  cleanup?: WorkflowConfigSection;
  diagnostics?: WorkflowConfigSection;
  cost?: WorkflowConfigSection;
  additional_settings?: WorkflowConfigSection;
  alignment?: WorkflowConfigSection;
  /** Advanced backend options not represented by the main UI are preserved here. */
  [extension: string]: WorkflowConfigSection | JsonValue | undefined;
};

export type RunRequest = {
  workflow: WorkflowName;
  inputPath: string;
  outputPath: string;
  configPath: string;
  envFile: string;
  audioTrack?: number;
  sidecarDir?: string;
  profile: boolean;
  sidecarsEnabled: boolean;
  cutSilenceEncoderPreset: CutSilenceEncoderPreset;
  silencePreviewHeight: 240 | 360 | 480 | 720;
  silencePreviewFps: 4 | 8 | 12 | 24;
};

export type RunEvent =
  | { type: "started"; runId: string; commandPreview: string; startedAt: string }
  | { type: "stdout"; runId: string; text: string }
  | { type: "stderr"; runId: string; text: string }
  | { type: "exit"; runId: string; code: number | null; signal: string | null; elapsedMs: number; cancelled: boolean }
  | { type: "error"; runId: string; message: string }
  | { type: "silence-candidates"; runId: string; workflow: WorkflowName; candidates: SilenceCutCandidate[] }
  | { type: "silence-review-required"; runId: string; reviewId: string; candidates: SilenceCutCandidate[] }
  | { type: "silence-cut-output"; runId: string; path: string };

export type RunState = "idle" | "running" | "reviewing" | "succeeded" | "failed" | "cancelled";

export type AppState = {
  settings: AppSettings;
  configs: Record<WorkflowName, WorkflowConfig>;
  configPaths: Record<WorkflowName, string>;
  projectRoot: string;
};

export type PathStatus = {
  path: string;
  exists: boolean;
};

export type AudioTrackInfo = {
  audioIndex: number;
  streamIndex: number;
  codec: string;
  sampleRate: number | null;
  channels: number | null;
  channelLayout: string;
  language: string;
  title: string;
};

export type MediaAnalysis = {
  durationSeconds: number | null;
  formatName: string;
  videoCodec: string;
  width: number | null;
  height: number | null;
  averageFrameRate: number | null;
  nominalFrameRate: number | null;
  frameRateMode: MediaFrameRateMode;
  thumbnailDataUrl: string;
  audioTracks: AudioTrackInfo[];
};
