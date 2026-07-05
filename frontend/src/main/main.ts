import { app, BrowserWindow, ipcMain, Menu, shell } from "electron";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { chooseDirectory, chooseExecutable, chooseFile, chooseInputFile, chooseOutputFile } from "./fileDialogs";
import { getEnvStatus } from "./envStatus";
import {
  loadAppState,
  readGlossary,
  readWorkflowConfig,
  saveAppSettings,
  saveGlossary,
  saveWorkflowConfig,
  workflowConfigPath
} from "./configStore";
import { cancelRun, startRun } from "./runProcess";
import { analyzeMedia } from "./mediaAnalyzer";
import { verifyHostedModels } from "./hostedModels";
import { downloadLocalProfile, listLocalProfiles, localModelStatus } from "./localModels";
import { checkLatestLlamaRelease, downloadManagedLlamaServer, getCurrentLlamaServerState, getManagedLlamaStatus, listLlamaBackends } from "./llamaServerManager";
import { runtimePaths } from "./paths";
import { createManagedPythonEnv, getPythonRuntimeStatus, installPythonRequirements } from "./pythonRuntime";
import { downloadManagedFfmpeg, getFfmpegStatus } from "./ffmpegManager";

let mainWindow: BrowserWindow | null = null;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 1080,
    minHeight: 720,
    autoHideMenuBar: true,
    backgroundColor: "#f5f3ef",
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (!app.isPackaged) {
    void mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173");
  } else {
    void mainWindow.loadFile(path.join(__dirname, "..", "..", "dist", "index.html"));
  }
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  registerIpc();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

function requireWindow(): BrowserWindow {
  if (!mainWindow) throw new Error("Main window is not ready");
  return mainWindow;
}

function registerIpc(): void {
  const paths = () => runtimePaths();
  ipcMain.handle("dialog:input-file", () => chooseInputFile(requireWindow()));
  ipcMain.handle("dialog:file", () => chooseFile(requireWindow()));
  ipcMain.handle("dialog:output-file", (_event, defaultPath?: string) => chooseOutputFile(requireWindow(), defaultPath));
  ipcMain.handle("dialog:directory", () => chooseDirectory(requireWindow()));
  ipcMain.handle("dialog:executable", () => chooseExecutable(requireWindow()));
  ipcMain.handle("state:get", () => loadAppState());
  ipcMain.handle("state:save-settings", (_event, settings) => saveAppSettings(settings));
  ipcMain.handle("config:get", (_event, workflow) => ({ config: readWorkflowConfig(workflow), path: workflowConfigPath(workflow) }));
  ipcMain.handle("config:save", (_event, workflow, config) => saveWorkflowConfig(workflow, config));
  ipcMain.handle("env:status", (_event, envFile: string) => getEnvStatus(envFile));
  ipcMain.handle("env:verify-hosted-models", (_event, envFile: string) => verifyHostedModels(envFile));
  ipcMain.handle("local-models:list", () => listLocalProfiles());
  ipcMain.handle("local-models:status", (_event, modelsDirectory: string, profileId: string) => localModelStatus(modelsDirectory, profileId));
  ipcMain.handle("local-models:download", (_event, modelsDirectory: string, profileId: string) => downloadLocalProfile(modelsDirectory, profileId, (text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "local-model-download", text });
  }));
  ipcMain.handle("llama:list-backends", () => listLlamaBackends());
  ipcMain.handle("llama:check-latest", () => checkLatestLlamaRelease());
  ipcMain.handle("llama:status", (_event, backend, releaseTag?: string) => getManagedLlamaStatus(paths().stateRoot, backend, releaseTag));
  ipcMain.handle("llama:current-state", (_event, serverPath: string) => getCurrentLlamaServerState(paths().stateRoot, serverPath));
  ipcMain.handle("llama:download", (_event, backend) => downloadManagedLlamaServer(paths().stateRoot, backend, (text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "llama-server-download", text });
  }));
  ipcMain.handle("glossary:read", () => readGlossary());
  ipcMain.handle("glossary:save", (_event, text: string) => saveGlossary(text));
  ipcMain.handle("path:exists", (_event, value: string) => Boolean(value && fs.existsSync(value)));
  ipcMain.handle("runtime:python-status", (_event, value: string) => {
    if (!value) return false;
    const result = spawnSync(value, ["--version"], { encoding: "utf8", timeout: 5000, windowsHide: true });
    return !result.error && result.status === 0;
  });
  ipcMain.handle("runtime:setup-status", async () => ({
    python: await getPythonRuntimeStatus(loadAppState().settings.pythonPath),
    ffmpeg: await getFfmpegStatus(),
  }));
  ipcMain.handle("runtime:create-managed-python", () => createManagedPythonEnv((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "python-runtime", text });
  }));
  ipcMain.handle("runtime:install-python-requirements", () => installPythonRequirements((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "python-runtime", text });
  }));
  ipcMain.handle("runtime:download-ffmpeg", () => downloadManagedFfmpeg((text) => {
    requireWindow().webContents.send("run:event", { type: "stdout", runId: "ffmpeg-download", text });
  }));
  ipcMain.handle("media:analyze", (_event, inputPath: string) => analyzeMedia(inputPath));
  ipcMain.handle("run:start", async (_event, request) => {
    const appState = loadAppState();
    const python = await getPythonRuntimeStatus(appState.settings.pythonPath);
    if (!python.ready) throw new Error(python.error || "Python runtime is not ready");
    if (!python.requirementsInstalled) {
      throw new Error(python.error || "Python runtime is missing required packages. Install Python requirements in Settings.");
    }
    return startRun(requireWindow(), paths(), python.resolvedPath, request);
  });
  ipcMain.handle("run:cancel", (_event, runId: string) => cancelRun(runId));
  ipcMain.handle("shell:open-path", (_event, target: string) => shell.openPath(target));
  ipcMain.handle("shell:show-item", (_event, target: string) => shell.showItemInFolder(target));
}
