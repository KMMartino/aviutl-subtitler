import type { AlignmentModelStatus, AppSettings, AppState, CurrentLlamaServerState, EncoderProbeResult, EnvStatus, FfmpegStatus, HostedModelVerification, HuggingFaceDownloaderStatus, LlamaBackendId, LlamaBackendOption, LlamaReleaseCheck, LocalModelProfile, LocalModelStatus, ManagedLlamaStatus, MediaAnalysis, PythonRuntimeStatus, RunEvent, RunRequest, RuntimeSetupStatus, SilenceCutDecision, WorkflowConfig, WorkflowName } from "./renderer/lib/types";

export {};

declare global {
  interface Window {
    subtitler: {
      chooseInputFile(defaultPath?: string): Promise<string | null>;
      chooseFile(): Promise<string | null>;
      chooseOutputFile(defaultPath?: string): Promise<string | null>;
      chooseDirectory(): Promise<string | null>;
      chooseExecutable(): Promise<string | null>;
      filePath(file: File): string;
      analyzeMedia(path: string): Promise<MediaAnalysis>;
      getAppState(): Promise<AppState>;
      resetAppState(): Promise<AppState>;
      saveAppSettings(settings: AppSettings): Promise<void>;
      getWorkflowConfig(workflow: WorkflowName): Promise<{ config: WorkflowConfig; path: string }>;
      saveWorkflowConfig(workflow: WorkflowName, config: WorkflowConfig): Promise<void>;
      getEnvStatus(envFile: string): Promise<EnvStatus>;
      verifyHostedModels(envFile: string): Promise<HostedModelVerification>;
      listLocalProfiles(): Promise<LocalModelProfile[]>;
      getLocalModelStatus(modelsDirectory: string, profileId: string): Promise<LocalModelStatus>;
      downloadLocalProfile(modelsDirectory: string, profileId: string, mode?: "direct" | "huggingface"): Promise<LocalModelStatus>;
      deleteManagedLocalProfile(modelsDirectory: string, profileId: string): Promise<LocalModelStatus>;
      getHuggingFaceDownloaderStatus(): Promise<HuggingFaceDownloaderStatus>;
      installHuggingFaceDownloader(): Promise<HuggingFaceDownloaderStatus>;
      listLlamaBackends(): Promise<LlamaBackendOption[]>;
      checkLatestLlamaRelease(): Promise<LlamaReleaseCheck>;
      getManagedLlamaStatus(backend: LlamaBackendId, releaseTag?: string): Promise<ManagedLlamaStatus>;
      getCurrentLlamaServerState(serverPath: string): Promise<CurrentLlamaServerState>;
      downloadManagedLlamaServer(backend: LlamaBackendId): Promise<ManagedLlamaStatus>;
      deleteManagedLlamaServer(backend: LlamaBackendId): Promise<ManagedLlamaStatus>;
      readGlossary(): Promise<string>;
      saveGlossary(text: string): Promise<void>;
      importGlossary(): Promise<string | null>;
      pathExists(path: string): Promise<boolean>;
      pythonReady(path: string): Promise<boolean>;
      getRuntimeSetupStatus(): Promise<RuntimeSetupStatus>;
      createManagedPythonEnv(): Promise<PythonRuntimeStatus>;
      installPythonRequirements(): Promise<PythonRuntimeStatus>;
      deleteManagedPythonEnv(): Promise<PythonRuntimeStatus>;
      downloadManagedFfmpeg(): Promise<FfmpegStatus>;
      deleteManagedFfmpeg(): Promise<FfmpegStatus>;
      downloadAlignmentModel(): Promise<AlignmentModelStatus>;
      deleteAlignmentModel(): Promise<AlignmentModelStatus>;
      startRun(request: RunRequest): Promise<{ runId: string }>;
      submitSilenceReview(runId: string, reviewId: string, decisions: Array<{ candidateId: string; decision: SilenceCutDecision }>): Promise<void>;
      probeCutSilenceEncoders(): Promise<EncoderProbeResult[]>;
      getSilenceSource(runId: string): Promise<{ url: string }>;
      getSilenceProxy(runId: string, candidateId: string, variant: "original" | "seam"): Promise<{ url: string }>;
      prefetchSilenceProxies(runId: string, candidateIds: string[]): Promise<void>;
      cancelRun(runId: string): Promise<void>;
      onRunEvent(callback: (event: RunEvent) => void): () => void;
      openPath(path: string): Promise<string>;
      showItemInFolder(path: string): Promise<void>;
    };
  }
}
