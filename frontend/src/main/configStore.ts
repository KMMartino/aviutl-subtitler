import fs from "node:fs";
import path from "node:path";
import type { AppSettings, AppState, WorkflowConfig, WorkflowName } from "../renderer/lib/types";
import { workflows } from "../renderer/lib/workflowLabels";
import { defaultPythonPath } from "./python";
import { runtimePaths, type RuntimePaths } from "./paths";

const legacyUserDataDirName = "subtitler-frontend";

export function stateRoot(paths = runtimePaths()): string {
  return paths.stateRoot;
}

export function configsRoot(paths = runtimePaths()): string {
  return paths.userConfigRoot;
}

export function settingsPath(paths = runtimePaths()): string {
  return path.join(paths.stateRoot, "settings.json");
}

export function defaultSettings(paths = runtimePaths()): AppSettings {
  return {
    pythonPath: defaultPythonPath(paths),
    envFile: paths.envFile,
    lastInputPath: "",
    lastOutputDir: "",
    lastSidecarDir: "",
    selectedWorkflow: "local",
    sidecarsEnabled: true,
    theme: "graphite",
    modelsDirectory: paths.userModelsRoot,
    localModelProfile: "16gb-gpu-gemma",
    llamaBackend: "vulkan",
    ffmpegMode: "auto",
    modelDownloadMode: "direct",
  };
}

export function ensureFrontendState(paths = runtimePaths()): void {
  migrateLegacyStateRoot(paths);
  fs.mkdirSync(configsRoot(paths), { recursive: true });
  if (!fs.existsSync(paths.envFile)) {
    fs.writeFileSync(paths.envFile, "", "utf8");
  }
  for (const workflow of workflows) {
    const userPath = workflowConfigPath(workflow, paths);
    if (!fs.existsSync(userPath)) {
      fs.copyFileSync(path.join(paths.bundledConfigRoot, `${workflow}.json`), userPath);
    }
  }
  if (!fs.existsSync(settingsPath(paths))) {
    writeJson(settingsPath(paths), defaultSettings(paths));
  }
  rewriteLegacyUserDataPaths(paths);
}

export function loadAppState(paths = runtimePaths()): AppState {
  ensureFrontendState(paths);
  const settings = { ...defaultSettings(paths), ...readJson<AppSettings>(settingsPath(paths)) };
  if (settings.localModelProfile === "14gb-gpu-gemma") {
    settings.localModelProfile = "12gb-gpu-gemma";
    writeJson(settingsPath(paths), settings);
  }
  const configs = Object.fromEntries(
    workflows.map((workflow) => [workflow, readWorkflowConfig(workflow, paths)])
  ) as Record<WorkflowName, WorkflowConfig>;
  const configPaths = Object.fromEntries(
    workflows.map((workflow) => [workflow, workflowConfigPath(workflow, paths)])
  ) as Record<WorkflowName, string>;
  return { settings, configs, configPaths, projectRoot: paths.appResourceRoot };
}

export function saveAppSettings(settings: AppSettings, paths = runtimePaths()): void {
  ensureFrontendState(paths);
  writeJson(settingsPath(paths), settings);
}

export function workflowConfigPath(workflow: WorkflowName, paths = runtimePaths()): string {
  return path.join(configsRoot(paths), `${workflow}.json`);
}

export function readWorkflowConfig(workflow: WorkflowName, paths = runtimePaths()): WorkflowConfig {
  ensureFrontendState(paths);
  return readJson<WorkflowConfig>(workflowConfigPath(workflow, paths));
}

export function saveWorkflowConfig(workflow: WorkflowName, config: WorkflowConfig, paths = runtimePaths()): void {
  ensureFrontendState(paths);
  writeJson(workflowConfigPath(workflow, paths), config);
}

export function glossaryPath(paths = runtimePaths()): string {
  return paths.glossaryFile;
}

export function readGlossary(paths = runtimePaths()): string {
  const file = glossaryPath(paths);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
}

export function saveGlossary(text: string, paths = runtimePaths()): void {
  fs.mkdirSync(path.dirname(glossaryPath(paths)), { recursive: true });
  fs.writeFileSync(glossaryPath(paths), text, "utf8");
}

export function importGlossary(sourcePath: string, paths = runtimePaths()): string {
  const destination = glossaryPath(paths);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(sourcePath, destination);
  return readGlossary(paths);
}

function readJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

function writeJson(file: string, value: unknown): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function migrateLegacyStateRoot(paths: RuntimePaths): void {
  if (!isSubUtlStateRoot(paths)) return;
  const legacyRoot = legacyStateRoot(paths);
  if (!fs.existsSync(legacyRoot) || fs.existsSync(paths.stateRoot)) return;
  fs.renameSync(legacyRoot, paths.stateRoot);
}

function rewriteLegacyUserDataPaths(paths: RuntimePaths): void {
  if (!isSubUtlStateRoot(paths)) return;
  const legacyRoot = legacyStateRoot(paths);
  const files = [
    settingsPath(paths),
    ...workflows.map((workflow) => workflowConfigPath(workflow, paths)),
  ];
  for (const file of files) {
    if (!fs.existsSync(file)) continue;
    const current = readJson<unknown>(file);
    const rewritten = rewriteStrings(current, legacyRoot, paths.stateRoot);
    if (JSON.stringify(rewritten) !== JSON.stringify(current)) {
      writeJson(file, rewritten);
    }
  }
}

function legacyStateRoot(paths: RuntimePaths): string {
  return path.join(path.dirname(paths.stateRoot), legacyUserDataDirName);
}

function isSubUtlStateRoot(paths: RuntimePaths): boolean {
  return path.basename(paths.stateRoot) === "SubUtl";
}

function rewriteStrings(value: unknown, from: string, to: string): unknown {
  if (typeof value === "string") {
    return value.split(from).join(to);
  }
  if (Array.isArray(value)) {
    return value.map((item) => rewriteStrings(item, from, to));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, rewriteStrings(item, from, to)])
    );
  }
  return value;
}
