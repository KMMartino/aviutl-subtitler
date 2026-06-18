import fs from "node:fs";
import path from "node:path";
import type { AppSettings, AppState, WorkflowConfig, WorkflowName } from "../renderer/lib/types";
import { workflows } from "../renderer/lib/workflowLabels";
import { defaultPythonPath, projectRoot } from "./python";

const stateDirName = ".frontend-state";

export function stateRoot(root = projectRoot()): string {
  return path.join(root, stateDirName);
}

export function configsRoot(root = projectRoot()): string {
  return path.join(stateRoot(root), "configs");
}

export function settingsPath(root = projectRoot()): string {
  return path.join(stateRoot(root), "settings.json");
}

export function defaultSettings(root = projectRoot()): AppSettings {
  return {
    pythonPath: defaultPythonPath(root),
    envFile: path.join(root, ".env"),
    lastInputPath: "",
    lastOutputDir: "",
    lastSidecarDir: "",
    selectedWorkflow: "local",
    sidecarsEnabled: true,
    theme: "graphite",
    modelsDirectory: path.join(root, ".frontend-state", "models"),
    localModelProfile: "16gb-gpu-gemma",
    llamaBackend: "vulkan"
  };
}

export function ensureFrontendState(root = projectRoot()): void {
  fs.mkdirSync(configsRoot(root), { recursive: true });
  for (const workflow of workflows) {
    const userPath = workflowConfigPath(workflow, root);
    if (!fs.existsSync(userPath)) {
      fs.copyFileSync(path.join(root, "configs", `${workflow}.json`), userPath);
    }
  }
  if (!fs.existsSync(settingsPath(root))) {
    writeJson(settingsPath(root), defaultSettings(root));
  }
}

export function loadAppState(root = projectRoot()): AppState {
  ensureFrontendState(root);
  const settings = { ...defaultSettings(root), ...readJson<AppSettings>(settingsPath(root)) };
  if (settings.localModelProfile === "14gb-gpu-gemma") {
    settings.localModelProfile = "12gb-gpu-gemma";
    writeJson(settingsPath(root), settings);
  }
  const configs = Object.fromEntries(
    workflows.map((workflow) => [workflow, readWorkflowConfig(workflow, root)])
  ) as Record<WorkflowName, WorkflowConfig>;
  const configPaths = Object.fromEntries(
    workflows.map((workflow) => [workflow, workflowConfigPath(workflow, root)])
  ) as Record<WorkflowName, string>;
  return { settings, configs, configPaths, projectRoot: root };
}

export function saveAppSettings(settings: AppSettings, root = projectRoot()): void {
  ensureFrontendState(root);
  writeJson(settingsPath(root), settings);
}

export function workflowConfigPath(workflow: WorkflowName, root = projectRoot()): string {
  return path.join(configsRoot(root), `${workflow}.json`);
}

export function readWorkflowConfig(workflow: WorkflowName, root = projectRoot()): WorkflowConfig {
  ensureFrontendState(root);
  return readJson<WorkflowConfig>(workflowConfigPath(workflow, root));
}

export function saveWorkflowConfig(workflow: WorkflowName, config: WorkflowConfig, root = projectRoot()): void {
  ensureFrontendState(root);
  writeJson(workflowConfigPath(workflow, root), config);
}

export function glossaryPath(root = projectRoot()): string {
  return path.join(root, "glossary.txt");
}

export function readGlossary(root = projectRoot()): string {
  const file = glossaryPath(root);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
}

export function saveGlossary(text: string, root = projectRoot()): void {
  fs.writeFileSync(glossaryPath(root), text, "utf8");
}

function readJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

function writeJson(file: string, value: unknown): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}
