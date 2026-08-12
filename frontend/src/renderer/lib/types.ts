export type WorkflowName = "local" | "hosted" | "local-long-stream" | "hosted-long-stream";
export type EditorialCheckpointSummary = {
  path: string;
  title: string;
  objective: string;
  status: string;
  sourceCount: number;
  updatedAtUtc: string;
};
export type EditorialGameSummary = {
  title: string;
  lastUsedAtUtc: string;
  revision: number;
};
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
  appLocale: AppLocale;
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
  ytDlpDenoPath?: string;
  ytDlpCookiesBrowser?: "" | "brave" | "chrome" | "chromium" | "edge" | "firefox" | "opera" | "safari" | "vivaldi" | "whale";
  ytDlpCookiesProfile?: string;
  modelDownloadMode?: "direct" | "huggingface";
  alignmentModel: string;
  alignmentOfflineModelCache: boolean;
  cutSilenceEncoderPreset: CutSilenceEncoderPreset;
  silencePreviewHeight: 240 | 360 | 480 | 720;
  silencePreviewFps: 4 | 8 | 12 | 24;
};

export type CutSilenceMode = "off" | "automatic" | "review";
export type BrollMode = "off" | "automatic";
export type EditorialSubtitleMode = "full" | "emphasis";
export type LongStreamTranscriptionScope = "full" | "high-activity";
export type EditorialMapMode = "off" | "suggestions";
export type CutSilenceEncoderPreset = "unconfigured" | "hevc-amf-cqp21" | "hevc-nvenc-qp21" | "hevc-qsv-q21" | "libx265-crf21";
export type SilenceCutDecision = "accept_cut" | "reject_cut" | "mark_and_reject";
export type BrollDecision = "describe" | "use_library" | "reject";
export type BrollReviewDecision = { candidateId: string; decision: BrollDecision; description?: string };
export type MediaFrameRateMode = "reported-cfr" | "possible-vfr" | "unknown";

export type SilenceCutCandidate = {
  id: string;
  silenceStart: number;
  silenceEnd: number;
  cutStart: number;
  cutEnd: number;
  cutDuration: number;
};

export type BrollCandidate = {
  id: string;
  assetId: string;
  assetPath: string;
  title: string;
  mediaKind: "video" | "image";
  startLine: number;
  endLine: number;
  sourceStartSec: number;
  sourceEndSec: number | null;
  confidence: number;
  reason: string;
  transcriptText: string;
  descriptionRequired: boolean;
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
  ytDlp: YtDlpStatus;
  alignment: AlignmentModelStatus;
};

export type YtDlpStatus = {
  ready: boolean;
  executablePath: string;
  version: string;
  managedInstalled: boolean;
  channel: "nightly";
  error: string;
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
      transcriptionGpt: boolean;
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
  longStream?: {
    transcriptionScope: LongStreamTranscriptionScope;
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
    brollMode?: BrollMode;
    editorialMapMode?: EditorialMapMode;
    editorialSubtitleMode?: EditorialSubtitleMode;
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
  workflow?: WorkflowConfigSection;
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
  editorialProject?: EditorialProjectRequest;
  editorialCheckpoint?: string;
  editorialCheckpointSources?: EditorialSourceSelection[];
  editorialRestartFrom?: EditorialRestartMode;
  editorialExtend?: boolean;
};

export type EditorialRestartBoundary =
  | "source_probe"
  | "transcription"
  | "visual_learning"
  | "semantic_spans"
  | "local_reconciliation"
  | "global_reconciliation";

export type EditorialRestartMode = "compatible" | EditorialRestartBoundary;

export type EditorialSourceSelection = {
  path: string;
  durationSeconds: number;
  mode: "single" | "paired";
  audioPath: string;
  visualPath: string;
  audioDurationSeconds: number;
  visualDurationSeconds: number;
  width: number | null;
  height: number | null;
  audioWidth: number | null;
  audioHeight: number | null;
  frameRate: number | null;
  audioFrameRate: number | null;
  pairingBasis: "single" | "filename" | "resolution" | "manual";
  roleConfirmed: boolean;
};

export type EditorialProjectRequest = {
  sources: EditorialSourceSelection[];
  titleOrGame: string;
  objective: string;
  targetDurationMinSeconds: number;
  targetDurationMaxSeconds: number;
  mustKeepNotes: string[];
  deEmphasizeNotes: string[];
  subtitleMode?: EditorialSubtitleMode;
  outputLocale?: AppLocale;
};

export type EditorialCheckpointInspection = {
  project_id: string;
  artifact_status: string;
  matches_sources: boolean;
  match_kind: "full" | "partial" | "none";
  matched_source_count: number;
  matched_selected_indices: number[];
  source_error: string;
  required_restart_from: EditorialRestartBoundary | null;
  next_incomplete: { source_id: string; source_name: string; stage: EditorialRestartBoundary; status: string } | null;
  recommended_restart_from: EditorialRestartMode;
  available_restart_from: EditorialRestartMode[];
  artifact_versions: Record<EditorialRestartBoundary, number>;
  current_versions: Record<EditorialRestartBoundary, number>;
  project_request: EditorialProjectRequest;
};

export type RunEvent =
  | { type: "started"; runId: string; commandPreview: string; startedAt: string }
  | { type: "stdout"; runId: string; text: string }
  | { type: "stderr"; runId: string; text: string }
  | { type: "exit"; runId: string; code: number | null; signal: string | null; elapsedMs: number; cancelled: boolean }
  | { type: "error"; runId: string; message: string }
  | { type: "silence-candidates"; runId: string; workflow: WorkflowName; candidates: SilenceCutCandidate[] }
  | { type: "silence-review-required"; runId: string; reviewId: string; candidates: SilenceCutCandidate[] }
  | { type: "broll-review-required"; runId: string; reviewId: string; candidates: BrollCandidate[] }
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

export type MediaLibraryRootKind = "referenced" | "managed";
export type MediaLibraryRootPurpose = "user" | "generated" | "web";
export type MediaAssetKind = "video" | "image";
export type MediaAssetAvailability = "active" | "missing" | "incompatible";
export type MediaTransparency = "present" | "absent" | "unsupported" | "unknown";
export type MediaAssetAnalysisState = "metadata_only" | "queued" | "analyzing" | "ready" | "failed" | "stale";
export type MediaAnalysisDetail = "simple" | "medium" | "detailed" | "precise" | "probe";

export type MediaLibraryRoot = {
  id: string;
  canonicalPath: string;
  kind: MediaLibraryRootKind;
  purpose: MediaLibraryRootPurpose;
  recursive: boolean;
  enabled: boolean;
  createdAt: string;
  lastScanAt: string;
  lastScanStatus: "never" | "running" | "complete" | "failed";
  lastError: string;
};

export type MediaAssetSummary = {
  id: string;
  rootId: string;
  canonicalPath: string;
  relativePath: string;
  relativeDirectory: string;
  mediaKind: MediaAssetKind;
  sourceKind: "local" | "web";
  availability: MediaAssetAvailability;
  analysisState: MediaAssetAnalysisState;
  title: string;
  effectiveDescription: string;
  userDescription: string;
  aiDescription: string;
  inferredDescription: string;
  tags: string[];
  sizeBytes: number;
  durationMs: number | null;
  width: number | null;
  height: number | null;
  videoCodec: string;
  audioCodec: string;
  hasAudio: boolean;
  transparency: MediaTransparency;
  mtimeNs: string;
  quickFingerprint: string;
  lastSeenAt: string;
  updatedAt: string;
};

export type MediaAssetSegment = {
  id: string;
  assetId: string;
  startMs: number;
  endMs: number;
  segmentKind: "shot" | "semantic_range";
  description: string;
  tags: string[];
  confidence: number;
  motionLevel: number | null;
  visualCategory: string;
  suitability: string;
  origin: "ai" | "user";
  locked: boolean;
};

export type MediaAssetDetail = MediaAssetSummary & {
  sourceUrl: string;
  sourcePageUrl: string;
  creator: string;
  licenseText: string;
  acquiredAt: string;
  segments: MediaAssetSegment[];
};

export type MediaAssetListRequest = {
  query?: string;
  rootId?: string;
  relativeDirectory?: string;
  mediaKind?: MediaAssetKind;
  availability?: MediaAssetAvailability;
  limit?: number;
  offset?: number;
};

export type MediaLibraryDirectory = {
  rootId: string;
  relativePath: string;
  name: string;
  depth: number;
  directFileCount: number;
  trackedFileCount: number;
  subtreeTrackedFileCount: number;
  included: boolean;
  subtreeEnabled: boolean;
  directEnabled: boolean;
  visible: boolean;
  directFilesVisible: boolean;
  hidden: boolean;
  managed: boolean;
  purpose: MediaLibraryRootPurpose;
};

export type MediaAssetListResult = {
  assets: MediaAssetSummary[];
  total: number;
};

export type MediaLibraryScanResult = {
  rootId: string;
  discovered: number;
  indexed: number;
  incompatible: number;
  missing: number;
  errors: string[];
};

export type WebAssetProbe = {
  sourceUrl: string;
  sourcePageUrl: string;
  title: string;
  creator: string;
  licenseText: string;
  durationSec: number | null;
  thumbnailUrl: string;
  extractor: string;
};

export type WebAssetAcquireRequest = {
  sourceUrl: string;
  description: string;
  rightsConfirmed: boolean;
  creator?: string;
  licenseText?: string;
  windowStartSec?: number;
};

export type MediaAssetAnalysisEstimate = {
  assetId: string;
  model: string;
  detail: MediaAnalysisDetail;
  recommended: boolean;
  sampleCount: number;
  maximumSampleCount: number;
  coarseSampleCount: number;
  maximumTransitionCount: number;
  adaptive: boolean;
  breakpointPrecisionSec: number | null;
  estimatedCostUsd: number;
  privacyNotice: string;
};

export type MediaAnalysisScope = {
  startMs: number;
  endMs: number;
};

export type MediaSegmentDescriptionInput = MediaAnalysisScope & {
  description: string;
};

export type MediaBulkAnalysisPlan = {
  assetIds: string[];
  estimates: Array<{
    detail: MediaAnalysisDetail;
    recommendedAssetCount: number;
    assetCount: number;
    sampleCount: number;
    estimatedCostUsd: number;
  }>;
  privacyNotice: string;
};

export type MediaAssetAnalysisResult = {
  asset: MediaAssetDetail;
  sampleCount: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
};

export type MediaAssetAnalysisPayload = {
  description: string;
  tags: string[];
  segments: Array<{
    start_ms: number;
    end_ms: number;
    description: string;
    tags: string[];
    confidence: number;
    motion_level: number | null;
    visual_category: string;
    suitability: string;
  }>;
  provider: string;
  model: string;
  prompt_version: string;
  sample_count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
};
import type { AppLocale } from "../../shared/i18n";
