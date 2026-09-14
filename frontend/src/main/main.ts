import { CreatorProjectStore } from "./creatorProjectStore";
import { acquireSource } from "./sourceAcquisition";
import { app, BrowserWindow, ipcMain, Menu, session, shell } from "electron";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";
import { chooseDirectory, chooseExecutable, chooseFile, chooseGlossaryFile, chooseInputFile, chooseInputFiles, chooseOutputFile } from "./fileDialogs";
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
import { cancelRun, shutdownActiveRun, startRun, submitBrollReview, submitSilenceReview } from "./runProcess";
import { MediaAnalysisCoordinator } from "./mediaAnalyzer";
import { assertTrustedSender, contentSecurityPolicy, installNavigationGuards, validateIpcArguments } from "./ipcSecurity";
import { verifyHostedModels } from "./hostedModels";
import { deleteManagedLocalProfile, downloadLocalProfile, getHuggingFaceDownloaderStatus, installHuggingFaceDownloader, listLocalProfiles, localModelStatus, verifyExistingLocalProfile } from "./localModels";
import { checkLatestLlamaRelease, deleteManagedLlamaBackend, downloadManagedLlamaServer, getCurrentLlamaServerState, getManagedLlamaStatus, listLlamaBackends, migrateLegacyManagedLlamaRoot } from "./llamaServerManager";
import { runtimePaths } from "./paths";
import { createManagedPythonEnv, deleteManagedPythonEnv, getPythonRuntimeStatus, installPythonRequirements } from "./pythonRuntime";
import { deleteManagedFfmpeg, downloadManagedFfmpeg, getFfmpegStatus, managedFfmpegBinDir } from "./ffmpegManager";
import { ALIGNMENT_MODEL, deleteAlignmentModel, downloadAlignmentModel, getAlignmentModelStatus } from "./alignmentModelManager";
import { CoalescedWriter } from "./coalescedWriter";
import type { AppSettings, EditorialSourceSelection, MediaAnalysisDetail, MediaAnalysisScope, MediaAssetKind, RunEvent, WorkflowConfig, WorkflowName } from "../renderer/lib/types";
import { userDataOverride } from "./userDataOverride";
import { probeCutSilenceEncoders } from "./cutSilenceManager";
import { registerSilenceMediaScheme, SilencePreviewManager } from "./silencePreviewManager";
import { MediaLibraryService } from "./mediaLibraryService";
import { deleteManagedYtDlp, getYtDlpStatus, installOrUpdateYtDlp } from "./ytDlpManager";
import { applyReviewedEditorialCuts, findMatchingEditorialCheckpoint, inspectEditorialCheckpoint } from "./editorialCheckpoint";
import { listEditorialCheckpoints, registerEditorialCheckpoint, removeEditorialCheckpoint } from "./editorialCheckpointRegistry";
import { listEditorialGames, rememberEditorialGame } from "./editorialGameRegistry";

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
let mediaLibrary: MediaLibraryService | null = null;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 1130,
    minWidth: 1080,
    minHeight: 720,
    autoHideMenuBar: true,
    backgroundColor: "#f5f3ef",
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

app.whenReady().then(async () => {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [contentSecurityPolicy(app.isPackaged)] } });
  });
  const currentPaths = runtimePaths();
  migrateLegacyManagedLlamaRoot(currentPaths.stateRoot, currentPaths.userToolsRoot);
  mediaLibrary = new MediaLibraryService(currentPaths);
  try {
    await mediaLibrary.initialize();
  } catch (error) {
    console.error("Media Library initialization failed; the rest of SubUtl will remain available.", error);
    await mediaLibrary.close().catch((closeError: unknown) => {
      console.error("Could not close the failed Media Library worker.", closeError);
    });
    mediaLibrary = null;
  }
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

let sourceAcquisition: AbortController | null = null;

app.on("before-quit", (event) => {
  shutdownActiveRun();
  sourceAcquisition?.abort();
  mediaAnalysis.cancel();
  silencePreview?.cleanupAll();
  if (quittingAfterDrain) return;
  event.preventDefault();
  void Promise.all([drainPersistence(), mediaLibrary?.close()]).finally(() => {
    quittingAfterDrain = true;
    app.quit();
  });
});

function requireWindow(): BrowserWindow {
  if (!mainWindow) throw new Error("Main window is not ready");
  return mainWindow;
}

function requireMediaLibrary(): MediaLibraryService {
  if (!mediaLibrary) throw new Error("Media library is not ready");
  return mediaLibrary;
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
  const projects = new CreatorProjectStore(paths().stateRoot, path.join(app.getPath("videos"), "SubUtl", "Projects"));
  handle("project:transcript", (_event, directory: string, resultId: string, file: string) => projects.transcript(directory, resultId, file));
  handle("project:export-exo", (_event, directory: string, resultId: string) => projects.exportExo(directory, resultId));
  handle("project:catalog", () => projects.catalog());
  handle("project:review", async (_event, directory: string, resultId: string, reviewedExo: string) => {
    const project = projects.open(directory);
    const parent = project.results.find((result) => result.id === resultId && result.workflow === "hosted-long-stream" && result.status === "complete" && !result.kind);
    if (!parent) throw new Error("Select a completed editing guide to import its reviewed EXO.");
    if (path.extname(reviewedExo).toLowerCase() !== ".exo") throw new Error("Choose an EXO exported from AviUtl.");
    const checkpoint = JSON.parse(fs.readFileSync(parent.outputPath, "utf8"));
    const mediaFiles = new Set(Array.from(new TextDecoder("shift_jis").decode(fs.readFileSync(reviewedExo)).matchAll(/^file=(.+)$/gm), (match) => match[1].trim().toLowerCase()));
    const sources: { source_id: string; visual_path: string }[] = checkpoint.sources ?? [];
    const present = sources.filter((source) => mediaFiles.has(source.visual_path.toLowerCase())).map((source) => source.source_id);
    const groups: string[][] = checkpoint.outputs?.exo_parts?.map((part: { source_ids: string[] }) => part.source_ids) ?? [sources.map((source) => source.source_id)];
    if (!present.length || !groups.some((group) => group.length === present.length && group.every((id) => present.includes(id)))) throw new Error("The reviewed EXO must contain one complete exported part's recordings.");
    const python = await currentPython();
    if (!python.ready || !python.requirementsInstalled) throw new Error("Python runtime is not ready.");
    const attempt = projects.begin(directory, "hosted-long-stream", null, parent.id);
    try {
      const imported = path.join(path.dirname(attempt.result.outputPath), "reviewed.exo");
      fs.copyFileSync(reviewedExo, imported, fs.constants.COPYFILE_EXCL);
      const applied = await applyReviewedEditorialCuts(paths(), python.resolvedPath, imported, (stream, text) => {
        requireWindow().webContents.send("run:event", { type: stream, runId: attempt.result.id, text });
      }, parent.outputPath);
      return await projects.finish(directory, attempt.result.id, "complete", applied.outputPath);
    } catch (error) { await projects.finish(directory, attempt.result.id, "failed"); throw error; }
  });
  handle("project:create", (_event, name: string, parent?: string) => projects.create(name, parent));
  handle("project:open", (_event, directory: string) => projects.open(directory));
  handle("project:update", (_event, update) => projects.update(update));
  handle("project:default-directory", (_event, directory: string) => projects.setDefaultDirectory(directory));
  const currentLocale = () => loadAppState().settings.appLocale;
  const currentPython = () => getPythonRuntimeStatus(loadAppState().settings.pythonPath);
  const currentYtDlp = async () => {
    const status = await getYtDlpStatus(paths());
    if (!status.ready) throw new Error(status.error || "Install managed yt-dlp in Settings before importing web media.");
    const settings = loadAppState().settings;
    return {
      executablePath: status.executablePath,
      denoPath: settings.ytDlpDenoPath?.trim(),
      cookiesBrowser: settings.ytDlpCookiesBrowser || undefined,
      cookiesProfile: settings.ytDlpCookiesProfile?.trim(),
      ffmpegLocation: managedFfmpegBinDir(paths()) || undefined,
    };
  };
  handle("dialog:input-file", (_event, defaultPath?: string) => chooseInputFile(requireWindow(), defaultPath, currentLocale()));
  handle("dialog:input-files", (_event, defaultPath?: string) => chooseInputFiles(requireWindow(), defaultPath, currentLocale()));
  handle("dialog:file", () => chooseFile(requireWindow(), currentLocale()));
  handle("dialog:output-file", (_event, defaultPath?: string) => chooseOutputFile(requireWindow(), defaultPath, currentLocale()));
  handle("dialog:directory", () => chooseDirectory(requireWindow()));
  handle("dialog:executable", () => chooseExecutable(requireWindow(), currentLocale()));
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
    const sourcePath = await chooseGlossaryFile(requireWindow(), currentLocale());
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
    ytDlp: await getYtDlpStatus(),
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
  handle("runtime:update-ytdlp", () => installOrUpdateYtDlp((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "yt-dlp-download", text });
  }));
  handle("runtime:delete-ytdlp", () => deleteManagedYtDlp());
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
  handle("library:list-roots", () => requireMediaLibrary().listRoots());
  handle("library:add-root", (_event, rootPath: string) => requireMediaLibrary().addRoot(rootPath));
  handle("library:set-root-enabled", (_event, rootId: string, enabled: boolean) => (
    requireMediaLibrary().setRootEnabled(rootId, enabled)
  ));
  handle("library:remove-root", (_event, rootId: string) => requireMediaLibrary().removeRoot(rootId));
  handle("library:list-directories", (_event, rootId: string) => requireMediaLibrary().listDirectories(rootId));
  handle("library:set-directory-included", (_event, rootId: string, relativeDirectory: string, included: boolean) => (
    requireMediaLibrary().setDirectoryIncluded(rootId, relativeDirectory, included)
  ));
  handle("library:set-directory-visible", (_event, rootId: string, relativeDirectory: string, kind: "subtree" | "direct", visible: boolean) => (
    requireMediaLibrary().setDirectoryVisible(rootId, relativeDirectory, kind, visible)
  ));
  handle("library:set-directory-hidden", (_event, rootId: string, relativeDirectory: string, hidden: boolean) => (
    requireMediaLibrary().setDirectoryHidden(rootId, relativeDirectory, hidden)
  ));
  handle("library:remove-directory-assets", (_event, rootId: string, relativeDirectory: string, deleteFiles: boolean) => (
    requireMediaLibrary().removeDirectoryAssets(rootId, relativeDirectory, deleteFiles)
  ));
  handle("library:scan", (_event, rootId: string) => requireMediaLibrary().scanRoot(rootId));
  handle("library:list-assets", (_event, request) => requireMediaLibrary().listAssets(request));
  handle("library:get-asset", (_event, assetId: string) => requireMediaLibrary().getAsset(assetId));
  handle("library:thumbnails", async (_event, assetIds: string[]) => {
    const thumbnailPaths = await requireMediaLibrary().thumbnailPaths(assetIds);
    return Object.fromEntries(
      Object.entries(thumbnailPaths).map(([assetId, thumbnailPath]) => [
        assetId,
        thumbnailPath ? silencePreview?.libraryMedia(thumbnailPath).url ?? "" : "",
      ]),
    );
  });
  handle("library:update-description", (_event, assetId: string, description: string) => (
    requireMediaLibrary().updateUserDescription(assetId, description)
  ));
  handle("library:add-segment", (_event, assetId: string, scope: MediaAnalysisScope, description: string) => (
    requireMediaLibrary().addUserSegment(assetId, scope, description)
  ));
  handle("source:acquire", async (_event, sourceUrl: string) => {
    if (sourceAcquisition) throw new Error("A source recording is already downloading.");
    const controller = new AbortController();
    sourceAcquisition = controller;
    try {
      return await acquireSource(await currentYtDlp(), path.join(path.dirname(paths().managedMediaRoot), "SubUtl Sources"), sourceUrl, controller.signal,
        (percent) => { if (!_event.sender.isDestroyed()) _event.sender.send("source:progress", percent); });
    } finally {
      sourceAcquisition = null;
    }
  });
  handle("source:cancel", () => { sourceAcquisition?.abort(); });
  handle("library:web-probe", async (_event, sourceUrl: string) => {
    return requireMediaLibrary().probeWebAsset(await currentYtDlp(), sourceUrl);
  });
  handle("library:web-acquire", async (_event, request) => {
    return requireMediaLibrary().acquireWebAsset(await currentYtDlp(), request);
  });
  handle("library:analysis-estimates", async (_event, assetId: string, scope?: MediaAnalysisScope) => {
    const hostedConfig = readWorkflowConfig("hosted");
    const model = String((hostedConfig.broll as Record<string, unknown> | undefined)?.analysis_model || "gpt-5.6-terra");
    return requireMediaLibrary().estimateAnalysis(assetId, model, scope);
  });
  handle("library:bulk-analysis-plan", async (_event, mediaKind: MediaAssetKind | "") => {
    const hostedConfig = readWorkflowConfig("hosted");
    const model = String((hostedConfig.broll as Record<string, unknown> | undefined)?.analysis_model || "gpt-5.6-terra");
    return requireMediaLibrary().bulkAnalysisPlan(mediaKind || undefined, model);
  });
  handle("library:analyze", async (_event, assetId: string, detail: MediaAnalysisDetail, scope?: MediaAnalysisScope) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready.");
    if (!python.requirementsInstalled) throw new Error("Install the current Python requirements before analyzing media.");
    const hostedConfig = readWorkflowConfig("hosted");
    const model = String((hostedConfig.broll as Record<string, unknown> | undefined)?.analysis_model || "gpt-5.6-terra");
    return requireMediaLibrary().analyzeAsset(assetId, python.resolvedPath, model, detail, appState.settings.envFile, scope);
  });
  handle("library:cancel-analysis", (_event, assetId: string) => requireMediaLibrary().cancelAnalysis(assetId));
  handle("silence:probe-encoders", () => probeCutSilenceEncoders());
  handle("silence:source", (_event, runId: string) => silencePreview?.source(runId));
  handle("silence:proxy", (_event, runId: string, candidateId: string, variant: "original" | "seam") => silencePreview?.proxy(runId, candidateId, variant));
  handle("silence:prefetch", (_event, runId: string, candidateIds: string[]) => silencePreview?.prefetch(runId, candidateIds));
  handle("broll:preview", (_event, runId: string, candidateId: string) => silencePreview?.brollPreview(runId, candidateId));
  handle("run:submit-silence-review", (_event, runId, reviewId, decisions) => submitSilenceReview(runId, reviewId, decisions));
  handle("run:submit-broll-review", (_event, runId, reviewId, decisions) => (
    submitBrollReview(
      runId,
      reviewId,
      decisions,
      (assetId, description) => requireMediaLibrary().updateUserDescription(assetId, description),
    )
  ));
  handle("editorial:inspect-checkpoint", async (_event, checkpoint: string, sources?: EditorialSourceSelection[]) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready");
    const inspection = await inspectEditorialCheckpoint(paths(), python.resolvedPath, checkpoint, sources);
    registerEditorialCheckpoint(paths(), checkpoint);
    return inspection;
  });
  handle("editorial:find-checkpoint", async (_event, sources: EditorialSourceSelection[]) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready");
    const match = await findMatchingEditorialCheckpoint(paths(), python.resolvedPath, sources);
    if (match) registerEditorialCheckpoint(paths(), match.path);
    return match;
  });
  handle("editorial:list-checkpoints", () => {
    const appState = loadAppState();
    return listEditorialCheckpoints(paths(), appState.settings.lastInputPath);
  });
  handle("editorial:remove-checkpoint", (_event, checkpoint: string) => removeEditorialCheckpoint(paths(), checkpoint));
  handle("editorial:apply-reviewed-cuts", async (_event, reviewProject: string) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready");
    const runId = crypto.randomUUID();
    const startedAt = Date.now();
    const target = requireWindow();
    const send = (event: RunEvent) => {
      if (!target.isDestroyed() && !target.webContents.isDestroyed()) {
        target.webContents.send("run:event", event);
      }
    };
    send({
      type: "started",
      runId,
      commandPreview: `Apply reviewed editorial project: ${reviewProject}`,
      startedAt: new Date(startedAt).toISOString(),
    });
    try {
      const matched = projects.findReviewedResult(reviewProject);
      const attempt = projects.begin(matched.project.directory, "hosted-long-stream", null, matched.result.id);
      let result;
      try {
        const imported = path.join(path.dirname(attempt.result.outputPath), "reviewed.exo");
        fs.copyFileSync(reviewProject, imported, fs.constants.COPYFILE_EXCL);
        result = await applyReviewedEditorialCuts(paths(), python.resolvedPath, imported,
          (stream, text) => send({ type: stream, runId, text }), matched.result.outputPath);
        await projects.finish(matched.project.directory, attempt.result.id, "complete", result.outputPath);
        result = { ...result, outputPath: attempt.result.deliverablePath!, reportPath: attempt.result.deliverablePath!.replace(/\.exo$/i, ".html") };
      } catch (error) {
        await projects.finish(matched.project.directory, attempt.result.id, "failed");
        throw error;
      }
      send({ type: "stdout", runId, text: `Narration report: ${result.reportPath}\nApplied EXO: ${result.outputPath}\n` });
      send({ type: "exit", runId, code: 0, signal: null, elapsedMs: Date.now() - startedAt, cancelled: false });
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      send({ type: "error", runId, message });
      send({ type: "exit", runId, code: 1, signal: null, elapsedMs: Date.now() - startedAt, cancelled: false });
      throw error;
    }
  });
  handle("editorial:list-games", () => listEditorialGames(paths()));
  handle("editorial:remember-game", (_event, title: string) => rememberEditorialGame(paths(), title));
  handle("run:start", async (_event, request) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready");
    if (!python.requirementsInstalled) {
      throw new Error(python.error || "Python runtime is missing required packages. Install Python requirements in Settings.");
    }
    const resuming = Boolean(request.creatorResumeResultId);
    const projectRun = request.creatorProjectDirectory
      ? resuming ? projects.resume(request.creatorProjectDirectory, request.creatorResumeResultId) : projects.begin(request.creatorProjectDirectory, request.workflow, request.creatorRecordingId ?? null) : null;
    try {
    if (projectRun && resuming) {
      request = { ...JSON.parse(fs.readFileSync(path.join(path.dirname(projectRun.result.sidecarDir), "request.json"), "utf8")), freshRun: false };
      if (request.editorialProject && fs.existsSync(projectRun.result.outputPath)) request = { ...request, editorialCheckpoint: projectRun.result.outputPath, editorialCheckpointSources: request.editorialProject.sources, editorialProject: undefined, editorialRestartFrom: "compatible" };
    }
    if (projectRun && !resuming) {
      request = { ...request, outputPath: projectRun.result.outputPath, deliverablePath: projectRun.result.deliverablePath, sidecarDir: projectRun.result.sidecarDir, sidecarsEnabled: true, transcriptArtifacts: request.freshRun || request.workflow === "hosted-long-stream" ? [] : projects.reusableTranscripts(projectRun.project, request.creatorRecordingId ?? null, request.audioTrack ?? 1) };
    }
    if (projectRun && !resuming && request.editorialProject && projects.seedEditorialResult(projectRun.project, projectRun.result)) {
      request = { ...request, editorialCheckpoint: projectRun.result.outputPath, editorialCheckpointSources: request.editorialProject.sources, editorialProject: undefined, editorialRestartFrom: "compatible", transcriptArtifacts: [] };
    }
    if (projectRun && !resuming) {
      const snapshot = path.join(path.dirname(projectRun.result.sidecarDir), "settings.json");
      fs.copyFileSync(request.configPath, snapshot, fs.constants.COPYFILE_EXCL);
      request = { ...request, configPath: snapshot };
      fs.writeFileSync(path.join(path.dirname(projectRun.result.sidecarDir), "request.json"), JSON.stringify(request, null, 2), { flag: "wx" });
    }
    if (request.editorialCheckpoint || request.editorialProject) {
      registerEditorialCheckpoint(paths(), request.editorialCheckpoint || request.outputPath);
      if (request.editorialProject?.titleOrGame) rememberEditorialGame(paths(), request.editorialProject.titleOrGame);
    }
    const result = startRun(requireWindow(), paths(), python.resolvedPath, request, {
      onControlEvent: (controlEvent) => {
        if (controlEvent.type === "silence-candidates" || controlEvent.type === "silence-review-required") {
          silencePreview?.setCandidates(controlEvent.runId, controlEvent.candidates);
        } else if (controlEvent.type === "broll-review-required") {
          silencePreview?.setBrollCandidates(controlEvent.runId, controlEvent.candidates);
        }
      },
      onFinish: async (runId, status) => {
        silencePreview?.cleanupRun(runId);
        if (projectRun) await projects.finish(projectRun.project.directory, projectRun.result.id, status);
      },
    });
    silencePreview?.registerRun(result.runId, request);
    return { ...result, project: projectRun?.project, outputPath: request.deliverablePath ?? request.outputPath, sidecarDir: request.sidecarDir };
    } catch (error) {
      if (projectRun) await projects.finish(projectRun.project.directory, projectRun.result.id, "failed");
      throw error;
    }
  });
  handle("run:cancel", (_event, runId: string, immediate = false) => cancelRun(runId, Boolean(immediate)));
  handle("shell:open-path", (_event, target: string) => shell.openPath(target));
  handle("shell:show-item", (_event, target: string) => shell.showItemInFolder(target));
}
