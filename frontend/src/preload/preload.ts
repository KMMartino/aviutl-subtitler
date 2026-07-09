import { contextBridge, ipcRenderer, webUtils } from "electron";
import type { AppSettings, LlamaBackendId, RunEvent, RunRequest, WorkflowConfig, WorkflowName } from "../renderer/lib/types";

contextBridge.exposeInMainWorld("subtitler", {
  chooseInputFile: () => ipcRenderer.invoke("dialog:input-file"),
  chooseFile: () => ipcRenderer.invoke("dialog:file"),
  chooseOutputFile: (defaultPath?: string) => ipcRenderer.invoke("dialog:output-file", defaultPath),
  chooseDirectory: () => ipcRenderer.invoke("dialog:directory"),
  chooseExecutable: () => ipcRenderer.invoke("dialog:executable"),
  filePath: (file: File) => webUtils.getPathForFile(file),
  analyzeMedia: (path: string) => ipcRenderer.invoke("media:analyze", path),
  getAppState: () => ipcRenderer.invoke("state:get"),
  saveAppSettings: (settings: AppSettings) => ipcRenderer.invoke("state:save-settings", settings),
  getWorkflowConfig: (workflow: WorkflowName) => ipcRenderer.invoke("config:get", workflow),
  saveWorkflowConfig: (workflow: WorkflowName, config: WorkflowConfig) => ipcRenderer.invoke("config:save", workflow, config),
  getEnvStatus: (envFile: string) => ipcRenderer.invoke("env:status", envFile),
  verifyHostedModels: (envFile: string) => ipcRenderer.invoke("env:verify-hosted-models", envFile),
  listLocalProfiles: () => ipcRenderer.invoke("local-models:list"),
  getLocalModelStatus: (modelsDirectory: string, profileId: string) => ipcRenderer.invoke("local-models:status", modelsDirectory, profileId),
  downloadLocalProfile: (modelsDirectory: string, profileId: string, mode?: "direct" | "huggingface") => ipcRenderer.invoke("local-models:download", modelsDirectory, profileId, mode),
  deleteManagedLocalProfile: (modelsDirectory: string, profileId: string) => ipcRenderer.invoke("local-models:delete-managed", modelsDirectory, profileId),
  getHuggingFaceDownloaderStatus: () => ipcRenderer.invoke("local-models:hf-downloader-status"),
  installHuggingFaceDownloader: () => ipcRenderer.invoke("local-models:install-hf-downloader"),
  listLlamaBackends: () => ipcRenderer.invoke("llama:list-backends"),
  checkLatestLlamaRelease: () => ipcRenderer.invoke("llama:check-latest"),
  getManagedLlamaStatus: (backend: LlamaBackendId, releaseTag?: string) => ipcRenderer.invoke("llama:status", backend, releaseTag),
  getCurrentLlamaServerState: (serverPath: string) => ipcRenderer.invoke("llama:current-state", serverPath),
  downloadManagedLlamaServer: (backend: LlamaBackendId) => ipcRenderer.invoke("llama:download", backend),
  deleteManagedLlamaServer: (backend: LlamaBackendId) => ipcRenderer.invoke("llama:delete-managed", backend),
  readGlossary: () => ipcRenderer.invoke("glossary:read"),
  saveGlossary: (text: string) => ipcRenderer.invoke("glossary:save", text),
  importGlossary: () => ipcRenderer.invoke("glossary:import"),
  pathExists: (path: string) => ipcRenderer.invoke("path:exists", path),
  pythonReady: (path: string) => ipcRenderer.invoke("runtime:python-status", path),
  getRuntimeSetupStatus: () => ipcRenderer.invoke("runtime:setup-status"),
  createManagedPythonEnv: () => ipcRenderer.invoke("runtime:create-managed-python"),
  installPythonRequirements: () => ipcRenderer.invoke("runtime:install-python-requirements"),
  deleteManagedPythonEnv: () => ipcRenderer.invoke("runtime:delete-managed-python"),
  downloadManagedFfmpeg: () => ipcRenderer.invoke("runtime:download-ffmpeg"),
  deleteManagedFfmpeg: () => ipcRenderer.invoke("runtime:delete-ffmpeg"),
  startRun: (request: RunRequest) => ipcRenderer.invoke("run:start", request),
  cancelRun: (runId: string) => ipcRenderer.invoke("run:cancel", runId),
  onRunEvent: (callback: (event: RunEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, runEvent: RunEvent) => callback(runEvent);
    ipcRenderer.on("run:event", listener);
    return () => ipcRenderer.removeListener("run:event", listener);
  },
  openPath: (path: string) => ipcRenderer.invoke("shell:open-path", path),
  showItemInFolder: (path: string) => ipcRenderer.invoke("shell:show-item", path)
});
