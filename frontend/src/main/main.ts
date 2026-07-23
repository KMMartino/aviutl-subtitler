import { app, BrowserWindow, ipcMain, Menu, session, shell } from "electron";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { chooseDirectory, chooseExecutable, chooseFile, chooseGlossaryFile, chooseInputFile, chooseOutputFile } from "./fileDialogs";
import { getEnvStatus } from "./envStatus";
import {
  loadAppState,
  importGlossary,
  readGlossary,
  readWorkflowConfig,
  resetFrontendState,
  saveAppSettings,
  saveActiveAlignmentModel,
  saveGlossary,
  saveWorkflowConfig,
  workflowConfigPath
} from "./configStore";
import { cancelRun, shutdownActiveRun, startRun, submitSilenceReview } from "./runProcess";
import { MediaAnalysisCoordinator } from "./mediaAnalyzer";
import { assertTrustedSender, contentSecurityPolicy, installNavigationGuards, validateIpcArguments } from "./ipcSecurity";
import { verifyHostedModels } from "./hostedModels";
import { deleteManagedLocalProfile, downloadLocalProfile, getHuggingFaceDownloaderStatus, installHuggingFaceDownloader, listLocalProfiles, localModelStatus, verifyExistingLocalProfile } from "./localModels";
import { checkLatestLlamaRelease, deleteManagedLlamaBackend, downloadManagedLlamaServer, getCurrentLlamaServerState, getManagedLlamaStatus, listLlamaBackends, migrateLegacyManagedLlamaRoot } from "./llamaServerManager";
import { runtimePaths } from "./paths";
import { createManagedPythonEnv, deleteManagedPythonEnv, getPythonRuntimeStatus, installPythonRequirements } from "./pythonRuntime";
import { deleteManagedFfmpeg, downloadManagedFfmpeg, getFfmpegStatus } from "./ffmpegManager";
import { ALIGNMENT_MODEL, deleteAlignmentModel, downloadAlignmentModel, getAlignmentModelStatus } from "./alignmentModelManager";
import { CoalescedWriter } from "./coalescedWriter";
import type { AppSettings, WorkflowConfig, WorkflowName } from "../renderer/lib/types";
import { userDataOverride } from "./userDataOverride";
import { probeCutSilenceEncoders } from "./cutSilenceManager";
import { registerSilenceMediaScheme, SilencePreviewManager } from "./silencePreviewManager";

registerSilenceMediaScheme();

const isolatedUserData = userDataOverride();
if (app.isPackaged) app.setName("SubUtl");
if (isolatedUserData) {
  // Test-only escape hatch used by packaged smoke checks. This must be set before
  // runtimePaths or any persisted state is read.
  app.setPath("userData", isolatedUserData);
} else if (app.isPackaged) {
  app.setPath("userData", path.join(app.getPath("appData"), "SubUtl"));
}

let mainWindow: BrowserWindow | null = null;
let drainPersistence = async (): Promise<void> => {};
let quittingAfterDrain = false;
const mediaAnalysis = new MediaAnalysisCoordinator();
let silencePreview: SilencePreviewManager | null = null;

function createWindow(): void {
  const frontend2 = process.env.SUBUTL_FRONTEND2 === "1";
  mainWindow = new BrowserWindow({
    width: 1280,
    height: frontend2 ? 840 : 1130,
    minWidth: frontend2 ? 1024 : 1080,
    minHeight: frontend2 ? 700 : 720,
    autoHideMenuBar: true,
    backgroundColor: frontend2 ? "#151918" : "#f5f3ef",
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });
  installNavigationGuards(mainWindow);

  if (!app.isPackaged) {
    void mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173");
  } else {
    void mainWindow.loadFile(path.join(__dirname, "..", "..", "dist", "index.html"));
  }
}

app.whenReady().then(() => {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [contentSecurityPolicy(app.isPackaged)] } });
  });
  const currentPaths = runtimePaths();
  migrateLegacyManagedLlamaRoot(currentPaths.stateRoot, currentPaths.userToolsRoot);
  Menu.setApplicationMenu(null);
  registerIpc();
  silencePreview = new SilencePreviewManager(path.join(app.getPath("temp"), "SubUtl-silence-preview"));
  silencePreview.initialize();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", (event) => {
  shutdownActiveRun();
  mediaAnalysis.cancel();
  silencePreview?.cleanupAll();
  if (quittingAfterDrain) return;
  event.preventDefault();
  void drainPersistence().finally(() => {
    quittingAfterDrain = true;
    app.quit();
  });
});

function requireWindow(): BrowserWindow {
  if (!mainWindow) throw new Error("Main window is not ready");
  return mainWindow;
}

function registerIpc(): void {
  const rawHandle = ipcMain.handle.bind(ipcMain);
  const trustedRendererUrl = app.isPackaged
    ? pathToFileURL(path.join(__dirname, "..", "..", "dist", "index.html")).href
    : process.env.VITE_DEV_SERVER_URL;
  const secureHandle: typeof ipcMain.handle = (channel, listener) => rawHandle(channel, (event, ...args) => {
    assertTrustedSender(event, app.isPackaged, trustedRendererUrl);
    validateIpcArguments(channel, args);
    return listener(event, ...args);
  });
  // Keep individual handlers compact while applying one mandatory policy boundary.
  const handle = secureHandle;
  const settingsWriter = new CoalescedWriter<AppSettings>((settings) => saveAppSettings(settings));
  const configWriters = new Map<WorkflowName, CoalescedWriter<WorkflowConfig>>();
  const configWriter = (workflow: WorkflowName) => {
    let writer = configWriters.get(workflow);
    if (!writer) {
      writer = new CoalescedWriter<WorkflowConfig>((config) => saveWorkflowConfig(workflow, config));
      configWriters.set(workflow, writer);
    }
    return writer;
  };
  drainPersistence = async () => {
    await settingsWriter.flushNow();
    await Promise.all([...configWriters.values()].map((writer) => writer.flushNow()));
  };
  const paths = () => runtimePaths();
  const currentPython = () => getPythonRuntimeStatus(loadAppState().settings.pythonPath);
  handle("dialog:input-file", (_event, defaultPath?: string) => chooseInputFile(requireWindow(), defaultPath));
  handle("dialog:file", () => chooseFile(requireWindow()));
  handle("dialog:output-file", (_event, defaultPath?: string) => chooseOutputFile(requireWindow(), defaultPath));
  handle("dialog:directory", () => chooseDirectory(requireWindow()));
  handle("dialog:executable", () => chooseExecutable(requireWindow()));
  handle("state:get", () => loadAppState());
  handle("state:reset", () => resetFrontendState());
  handle("state:save-settings", (_event, settings) => settingsWriter.enqueue(settings));
  handle("config:get", (_event, workflow) => ({ config: readWorkflowConfig(workflow), path: workflowConfigPath(workflow) }));
  handle("config:save", (_event, workflow: WorkflowName, config: WorkflowConfig) => configWriter(workflow).enqueue(config));
  handle("env:status", (_event, envFile: string) => getEnvStatus(envFile));
  handle("env:verify-hosted-models", (_event, envFile: string) => verifyHostedModels(envFile));
  handle("local-models:list", () => listLocalProfiles());
  handle("local-models:status", (_event, modelsDirectory: string, profileId: string) => localModelStatus(modelsDirectory, profileId, paths().userModelsRoot));
  handle("local-models:download", async (_event, modelsDirectory: string, profileId: string, mode?: "direct" | "huggingface") => {
    const onLog = (text: string) => requireWindow().webContents.send("run:event", { type: "stdout", runId: "local-model-download", text });
    const status = localModelStatus(modelsDirectory, profileId, paths().userModelsRoot);
    return status.needsVerification
      ? verifyExistingLocalProfile(modelsDirectory, profileId, paths().userModelsRoot, onLog)
      : downloadLocalProfile(modelsDirectory, profileId, onLog, paths().userModelsRoot, mode ?? "direct", paths(), mode === "huggingface" ? await currentPython() : undefined);
  });
  handle("local-models:delete-managed", (_event, modelsDirectory: string, profileId: string) => deleteManagedLocalProfile(modelsDirectory, profileId, paths().userModelsRoot));
  handle("local-models:hf-downloader-status", async () => getHuggingFaceDownloaderStatus(paths(), await currentPython()));
  handle("local-models:install-hf-downloader", async () => installHuggingFaceDownloader(paths(), await currentPython(), (text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "hf-downloader-install", text });
  }));
  handle("llama:list-backends", () => listLlamaBackends());
  handle("llama:check-latest", () => checkLatestLlamaRelease());
  handle("llama:status", (_event, backend, releaseTag?: string) => getManagedLlamaStatus(paths().userToolsRoot, backend, releaseTag));
  handle("llama:current-state", (_event, serverPath: string) => getCurrentLlamaServerState(paths().userToolsRoot, serverPath));
  handle("llama:download", (_event, backend) => downloadManagedLlamaServer(paths().userToolsRoot, backend, (text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "llama-server-download", text });
  }));
  handle("llama:delete-managed", (_event, backend) => deleteManagedLlamaBackend(paths().userToolsRoot, backend));
  handle("glossary:read", () => readGlossary());
  handle("glossary:save", (_event, text: string) => saveGlossary(text));
  handle("glossary:import", async () => {
    const sourcePath = await chooseGlossaryFile(requireWindow());
    return sourcePath ? importGlossary(sourcePath) : null;
  });
  handle("path:exists", (_event, value: string) => Boolean(value && fs.existsSync(value)));
  handle("runtime:python-status", (_event, value: string) => {
    if (!value) return false;
    const result = spawnSync(value, ["--version"], { encoding: "utf8", timeout: 5000, windowsHide: true });
    return !result.error && result.status === 0;
  });
  handle("runtime:setup-status", async () => ({
    python: await getPythonRuntimeStatus(loadAppState().settings.pythonPath),
    ffmpeg: await getFfmpegStatus(),
    alignment: await getAlignmentModelStatus(paths()),
  }));
  handle("runtime:create-managed-python", () => createManagedPythonEnv((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "python-runtime", text });
  }));
  handle("runtime:install-python-requirements", () => installPythonRequirements((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "python-runtime", text });
  }));
  handle("runtime:delete-managed-python", () => deleteManagedPythonEnv());
  handle("runtime:download-ffmpeg", () => downloadManagedFfmpeg((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "ffmpeg-download", text });
  }));
  handle("runtime:delete-ffmpeg", () => deleteManagedFfmpeg());
  handle("runtime:download-alignment", async () => {
    const status = await downloadAlignmentModel(paths(), await currentPython(), (text) => {
      requireWindow().webContents.send("run:event", { type: "stdout", runId: "alignment-model-download", text });
    });
    await drainPersistence();
    saveActiveAlignmentModel(status.modelPath, true, paths());
    return status;
  });
  handle("runtime:delete-alignment", async () => {
    const status = await deleteAlignmentModel(paths());
    await drainPersistence();
    saveActiveAlignmentModel(ALIGNMENT_MODEL.repo, false, paths());
    return status;
  });
  handle("media:analyze", (_event, inputPath: string) => mediaAnalysis.analyze(inputPath));
  handle("silence:probe-encoders", () => probeCutSilenceEncoders());
  handle("silence:source", (_event, runId: string) => silencePreview?.source(runId));
  handle("silence:proxy", (_event, runId: string, candidateId: string, variant: "original" | "seam") => silencePreview?.proxy(runId, candidateId, variant));
  handle("silence:prefetch", (_event, runId: string, candidateIds: string[]) => silencePreview?.prefetch(runId, candidateIds));
  handle("run:submit-silence-review", (_event, runId, reviewId, decisions) => submitSilenceReview(runId, reviewId, decisions));
  handle("run:start", async (_event, request) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready");
    if (!python.requirementsInstalled) {
      throw new Error(python.error || "Python runtime is missing required packages. Install Python requirements in Settings.");
    }
    const result = startRun(requireWindow(), paths(), python.resolvedPath, request, {
      onControlEvent: (controlEvent) => {
        if (controlEvent.type === "silence-candidates" || controlEvent.type === "silence-review-required") {
          silencePreview?.setCandidates(controlEvent.runId, controlEvent.candidates);
        }
      },
      onFinish: (runId) => silencePreview?.cleanupRun(runId),
    });
    silencePreview?.registerRun(result.runId, request);
    return result;
  });
  handle("run:cancel", (_event, runId: string) => cancelRun(runId));
  handle("shell:open-path", (_event, target: string) => shell.openPath(target));
  handle("shell:show-item", (_event, target: string) => shell.showItemInFolder(target));
}
