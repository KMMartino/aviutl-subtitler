import fs from "node:fs";
import path from "node:path";
import type { AppSettings, AppState, WorkflowConfig, WorkflowName } from "../renderer/lib/types";
import { workflows } from "../renderer/lib/workflowLabels";
import { APPROVED_MODELS, hostedCleanupTuning, recommendedFallbackTranscription } from "../shared/hostedModelCatalog";
import { defaultPythonPath } from "./python";
import { runtimePaths, type RuntimePaths } from "./paths";

const legacyUserDataDirName = "subtitler-frontend";
const settingsSchemaVersion = 3;
const remoteAlignmentModel = "MahmoudAshraf/mms-300m-1130-forced-aligner";
const appStateMarkers = ["settings.json", "settings.json.bak", "configs", ".env", "glossary.txt", "tools", "models", "python"];

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
    schemaVersion: settingsSchemaVersion,
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
    alignmentModel: remoteAlignmentModel,
    alignmentOfflineModelCache: false,
    cutSilenceEncoderPreset: "unconfigured",
    silencePreviewHeight: 360,
    silencePreviewFps: 8,
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
    atomicWriteJson(settingsPath(paths), defaultSettings(paths));
  }
  migrateHostedDefaults(paths);
  rewriteLegacyUserDataPaths(paths);
}

export function loadAppState(paths = runtimePaths()): AppState {
  ensureFrontendState(paths);
  const settings = migrateAndValidateSettings(readRecoverableJson(settingsPath(paths)), paths);
  if (settings.localModelProfile === "14gb-gpu-gemma") {
    settings.localModelProfile = "12gb-gpu-gemma";
    atomicWriteJson(settingsPath(paths), settings);
  }
  const configs = Object.fromEntries(
    workflows.map((workflow) => {
      const current = readWorkflowConfig(workflow, paths);
      const shared = withSharedAlignment(current, settings);
      if (JSON.stringify(current) !== JSON.stringify(shared)) atomicWriteJson(workflowConfigPath(workflow, paths), shared);
      return [workflow, shared];
    })
  ) as Record<WorkflowName, WorkflowConfig>;
  const configPaths = Object.fromEntries(
    workflows.map((workflow) => [workflow, workflowConfigPath(workflow, paths)])
  ) as Record<WorkflowName, string>;
  return { settings, configs, configPaths, projectRoot: paths.appResourceRoot };
}

export function saveAppSettings(settings: AppSettings, paths = runtimePaths()): void {
  ensureFrontendState(paths);
  atomicWriteJson(settingsPath(paths), validateSettings(settings, paths));
}

export function resetFrontendState(paths = runtimePaths()): AppState {
  const suffix = `.invalid-${Date.now()}`;
  for (const file of [settingsPath(paths), ...workflows.map((workflow) => workflowConfigPath(workflow, paths))]) {
    if (fs.existsSync(file)) fs.renameSync(file, `${file}${suffix}`);
    fs.rmSync(`${file}.bak`, { force: true });
  }
  return loadAppState(paths);
}

export function workflowConfigPath(workflow: WorkflowName, paths = runtimePaths()): string {
  if (!workflows.includes(workflow)) throw new Error("Invalid workflow name.");
  return path.join(configsRoot(paths), `${workflow}.json`);
}

export function readWorkflowConfig(workflow: WorkflowName, paths = runtimePaths()): WorkflowConfig {
  ensureFrontendState(paths);
  return validateWorkflowConfig(readRecoverableJson(workflowConfigPath(workflow, paths)), workflow);
}

export function saveWorkflowConfig(workflow: WorkflowName, config: WorkflowConfig, paths = runtimePaths()): void {
  ensureFrontendState(paths);
  atomicWriteJson(workflowConfigPath(workflow, paths), validateWorkflowConfig(config, workflow));
}

export function saveActiveAlignmentModel(model: string, offlineModelCache: boolean, paths = runtimePaths()): void {
  const state = loadAppState(paths);
  saveAppSettings({ ...state.settings, alignmentModel: model, alignmentOfflineModelCache: offlineModelCache }, paths);
  for (const workflow of workflows) {
    saveWorkflowConfig(workflow, withAlignmentModel(state.configs[workflow], model, offlineModelCache), paths);
  }
}

export function withAlignmentModel(config: WorkflowConfig, model: string, offlineModelCache: boolean): WorkflowConfig {
  return {
    ...config,
    alignment: {
      ...(config.alignment ?? {}),
      model,
      offline_model_cache: offlineModelCache,
    },
  };
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

function atomicWriteJson(file: string, value: unknown): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  const backup = `${file}.bak`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
    if (fs.existsSync(file)) fs.copyFileSync(file, backup);
    fs.renameSync(temporary, file);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function readRecoverableJson(file: string): unknown {
  try {
    return readJson<unknown>(file);
  } catch (primaryError) {
    const backup = `${file}.bak`;
    try {
      const recovered = readJson<unknown>(backup);
      restorePrimaryJson(file, recovered);
      return recovered;
    } catch {
      const message = primaryError instanceof Error ? primaryError.message : String(primaryError);
      throw new Error(`Could not load ${file}. The file and its backup are invalid. Reset or restore this state file. (${message})`);
    }
  }
}

function restorePrimaryJson(file: string, value: unknown): void {
  const temporary = `${file}.${process.pid}.recovery.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
    fs.renameSync(temporary, file);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validateSettings(value: unknown, paths: RuntimePaths): AppSettings {
  if (!isObject(value)) throw new Error("Settings must be a JSON object.");
  const defaults = defaultSettings(paths);
  const merged = { ...defaults, ...value } as Record<string, unknown>;
  if (!workflows.includes(merged.selectedWorkflow as WorkflowName)) throw new Error("Settings contain an invalid workflow.");
  if (!["paper", "sage", "sky", "rose", "graphite", "forest", "midnight", "plum"].includes(String(merged.theme))) throw new Error("Settings contain an invalid theme.");
  if (!["vulkan", "cuda-12"].includes(String(merged.llamaBackend))) throw new Error("Settings contain an invalid llama backend.");
  if (!["auto", "managed", "path"].includes(String(merged.ffmpegMode))) throw new Error("Settings contain an invalid FFmpeg mode.");
  if (!["direct", "huggingface"].includes(String(merged.modelDownloadMode))) throw new Error("Settings contain an invalid model download mode.");
  if (!["unconfigured", "hevc-amf-cqp21", "hevc-nvenc-qp21", "hevc-qsv-q21", "libx265-crf21"].includes(String(merged.cutSilenceEncoderPreset))) throw new Error("Settings contain an invalid Cut silence encoder.");
  if (![240, 360, 480, 720].includes(Number(merged.silencePreviewHeight))) throw new Error("Settings contain an invalid silence preview height.");
  if (![4, 8, 12, 24].includes(Number(merged.silencePreviewFps))) throw new Error("Settings contain an invalid silence preview frame rate.");
  for (const key of ["pythonPath", "envFile", "lastInputPath", "lastOutputDir", "lastSidecarDir", "modelsDirectory", "localModelProfile", "alignmentModel"] as const) {
    if (typeof merged[key] !== "string") throw new Error(`Settings field ${key} must be a string.`);
  }
  for (const key of ["sidecarsEnabled", "alignmentOfflineModelCache"] as const) {
    if (typeof merged[key] !== "boolean") throw new Error(`Settings field ${key} must be a boolean.`);
  }
  return { ...merged, schemaVersion: settingsSchemaVersion } as AppSettings;
}

function migrateAndValidateSettings(value: unknown, paths: RuntimePaths): AppSettings {
  if (!isObject(value)) return validateSettings(value, paths);
  const migrated = { ...value };
  if (typeof migrated.alignmentModel !== "string") {
    let legacySelection: { model: string; offline: boolean } | undefined;
    for (const workflow of workflows) {
      const file = workflowConfigPath(workflow, paths);
      if (!fs.existsSync(file)) continue;
      try {
        const config = readRecoverableJson(file);
        if (isObject(config) && isObject(config.alignment) && typeof config.alignment.model === "string") {
          const candidate = { model: config.alignment.model, offline: config.alignment.offline_model_cache === true };
          if (!legacySelection || candidate.offline || candidate.model !== remoteAlignmentModel) legacySelection = candidate;
          if (candidate.offline) break;
        }
      } catch { /* surfaced when configs are loaded */ }
    }
    if (legacySelection) {
      migrated.alignmentModel = legacySelection.model;
      migrated.alignmentOfflineModelCache = legacySelection.offline;
    }
  }
  const settings = validateSettings(migrated, paths);
  if (JSON.stringify(settings) !== JSON.stringify(value)) atomicWriteJson(settingsPath(paths), settings);
  return settings;
}

function validateWorkflowConfig(value: unknown, workflow: WorkflowName): WorkflowConfig {
  if (!isObject(value)) throw new Error(`${workflow} configuration must be a JSON object.`);
  if (!isJsonValue(value)) throw new Error(`${workflow} configuration contains an unsupported or non-finite value.`);
  for (const section of ["workflow", "audio", "backend", "cleanup", "diagnostics", "cost", "additional_settings", "alignment", "vad", "subtitles", "exo"]) {
    if (value[section] !== undefined && !isObject(value[section])) throw new Error(`${workflow}.${section} must be a JSON object.`);
  }
  return value as WorkflowConfig;
}

function isJsonValue(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isObject(value) && Object.values(value).every(isJsonValue);
}

function withSharedAlignment(config: WorkflowConfig, settings: AppSettings): WorkflowConfig {
  return withAlignmentModel(config, settings.alignmentModel, settings.alignmentOfflineModelCache);
}

function migrateLegacyStateRoot(paths: RuntimePaths): void {
  if (!isSubUtlStateRoot(paths)) return;
  const legacyRoot = legacyStateRoot(paths);
  if (!fs.existsSync(legacyRoot)) return;
  if (!fs.existsSync(paths.stateRoot)) {
    fs.renameSync(legacyRoot, paths.stateRoot);
    return;
  }

  // Electron creates cache and Chromium-internal entries before application
  // state is loaded. Those entries alone must not suppress legacy migration.
  // Any actual SubUtl marker makes the destination authoritative.
  if (appStateMarkers.some((marker) => fs.existsSync(path.join(paths.stateRoot, marker)))) return;

  const legacyEntries = fs.readdirSync(legacyRoot);
  const collisions = legacyEntries.filter((entry) => fs.existsSync(path.join(paths.stateRoot, entry)));
  if (collisions.length > 0) {
    // Preflight all targets so a collision cannot produce a partial migration.
    throw new Error(`Could not migrate legacy SubUtl state because destination entries already exist: ${collisions.join(", ")}`);
  }
  for (const entry of legacyEntries) {
    fs.renameSync(path.join(legacyRoot, entry), path.join(paths.stateRoot, entry));
  }
  fs.rmdirSync(legacyRoot);
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
    const current = readRecoverableJson(file);
    const rewritten = rewriteStrings(current, legacyRoot, paths.stateRoot);
    if (JSON.stringify(rewritten) !== JSON.stringify(current)) {
      atomicWriteJson(file, rewritten);
    }
  }
}

function migrateHostedDefaults(paths: RuntimePaths): void {
  for (const workflow of ["hosted", "hosted-long-stream"] as const) {
    const file = workflowConfigPath(workflow, paths);
    if (!fs.existsSync(file)) continue;
    const config = validateWorkflowConfig(readRecoverableJson(file), workflow);
    let changed = false;
    const oldHostedDefaultFallback = (
      config.backend?.transcriber === "gemini"
      && config.backend.transcription_model === APPROVED_MODELS.gemini
      && config.backend.fallback_transcriber === "openai"
      && config.backend.fallback_transcription_model === APPROVED_MODELS.openaiTranscriptionMini
    );
    const oldHostedDefaultCleanup = (
      config.cleanup?.backend === "openai"
      && config.cleanup.api_model === APPROVED_MODELS.openaiCleanup
      && (config.cleanup.window_subtitles === 8 || config.cleanup.window_subtitles === 256 || config.cleanup.window_subtitles === undefined)
    );
    if (
      oldHostedDefaultFallback
    ) {
      const fallback = recommendedFallbackTranscription("gemini", APPROVED_MODELS.gemini);
      config.backend!.fallback_transcriber = fallback.provider;
      config.backend!.fallback_transcription_model = fallback.model;
      changed = true;
    }
    if (oldHostedDefaultFallback && oldHostedDefaultCleanup && config.cleanup?.skip_final_review === true) {
      config.cleanup.skip_final_review = false;
      changed = true;
    }
    if (config.cleanup?.backend === "openai" || config.cleanup?.backend === "gemini") {
      let tuning = hostedCleanupTuning(config.cleanup.backend, String(config.cleanup.api_model ?? ""));
      if (!tuning) {
        config.cleanup.api_model = config.cleanup.backend === "gemini"
          ? APPROVED_MODELS.gemini
          : APPROVED_MODELS.openaiCleanup;
        tuning = hostedCleanupTuning(config.cleanup.backend, String(config.cleanup.api_model));
        changed = true;
      }
      if (config.cleanup.reasoning_effort !== tuning?.reasoningEffort) {
        config.cleanup.reasoning_effort = tuning?.reasoningEffort ?? null;
        changed = true;
      }
      if (config.cleanup.thinking_level !== tuning?.thinkingLevel) {
        config.cleanup.thinking_level = tuning?.thinkingLevel ?? null;
        changed = true;
      }
    }
    if (changed) atomicWriteJson(file, config);
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
