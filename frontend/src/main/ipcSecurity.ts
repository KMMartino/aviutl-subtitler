import path from "node:path";
import type { IpcMainInvokeEvent } from "electron";

const workflows = new Set(["local", "hosted", "local-long-stream", "hosted-long-stream"]);
const backends = new Set(["vulkan", "cuda-12"]);
const noArgs = new Set([
  "dialog:file", "dialog:directory", "dialog:executable", "state:get", "state:reset",
  "local-models:list", "local-models:hf-downloader-status", "local-models:install-hf-downloader", "llama:list-backends",
  "llama:check-latest", "glossary:read", "glossary:import", "runtime:setup-status", "runtime:create-managed-python",
  "runtime:install-python-requirements", "runtime:delete-managed-python", "runtime:download-ffmpeg", "runtime:delete-ffmpeg",
  "runtime:update-ytdlp", "runtime:delete-ytdlp",
  "runtime:download-alignment", "runtime:delete-alignment",
  "silence:probe-encoders",
  "library:list-roots",
  "editorial:list-checkpoints",
  "editorial:list-games",
]);

export function assertTrustedSender(event: Pick<IpcMainInvokeEvent, "senderFrame">, packaged: boolean, expectedUrl?: string): void {
  const actual = event.senderFrame?.url ?? "";
  if (packaged) {
    try {
      const actualUrl = new URL(actual);
      const trustedUrl = new URL(expectedUrl ?? "file:///invalid");
      if (actualUrl.protocol !== "file:" || actualUrl.pathname !== trustedUrl.pathname) throw new Error();
    } catch {
      throw new Error("Blocked IPC from an untrusted renderer");
    }
    return;
  }
  const expected = new URL(expectedUrl ?? "http://127.0.0.1:5173").origin;
  let origin = "";
  try { origin = new URL(actual).origin; } catch { /* rejected below */ }
  if (origin !== expected) throw new Error("Blocked IPC from an untrusted renderer");
}

export function validateIpcArguments(channel: string, args: unknown[]): void {
  if (noArgs.has(channel)) return exact(args, 0);
  switch (channel) {
    case "dialog:input-file": case "dialog:input-files": case "dialog:output-file": return optionalAbsolutePath(args);
    case "state:save-settings": return objectArg(args);
    case "config:get": return enumArg(args, workflows);
    case "config:save": exact(args, 2); assertEnum(args[0], workflows); assertPlainObject(args[1]); return;
    case "env:status": case "env:verify-hosted-models": case "path:exists": case "runtime:python-status":
    case "media:analyze": case "shell:open-path": case "shell:show-item": return absolutePathArg(args);
    case "local-models:status": case "local-models:delete-managed": exact(args, 2); assertAbsolutePath(args[0]); assertShortString(args[1]); return;
    case "local-models:download": exactRange(args, 2, 3); assertAbsolutePath(args[0]); assertShortString(args[1]); if (args[2] !== undefined && args[2] !== "direct" && args[2] !== "huggingface") fail(); return;
    case "llama:status": exactRange(args, 1, 2); assertEnum(args[0], backends); if (args[1] !== undefined) assertShortString(args[1]); return;
    case "llama:current-state": return absolutePathArg(args);
    case "llama:download": case "llama:delete-managed": return enumArg(args, backends);
    case "glossary:save": exact(args, 1); if (typeof args[0] !== "string" || args[0].length > 5_000_000) fail(); return;
    case "run:cancel": exact(args, 1); assertShortString(args[0]); return;
    case "silence:source": return shortStringArg(args);
    case "broll:preview":
      exact(args, 2); assertShortString(args[0]); assertShortString(args[1]); return;
    case "silence:proxy": exact(args, 3); assertShortString(args[0]); assertShortString(args[1]); assertEnum(args[2], new Set(["original", "seam"])); return;
    case "silence:prefetch": exact(args, 2); assertShortString(args[0]); if (!Array.isArray(args[1]) || args[1].length > 2) fail(); for (const item of args[1]) assertShortString(item); return;
    case "run:submit-silence-review": return validateSilenceReview(args);
    case "run:submit-broll-review": return validateBrollReview(args);
    case "library:add-root": return absolutePathArg(args);
    case "library:set-root-enabled":
      exact(args, 2); assertShortString(args[0]); if (typeof args[1] !== "boolean") fail(); return;
    case "library:scan": case "library:get-asset": case "library:remove-root": case "library:cancel-analysis":
    case "library:list-directories": return shortStringArg(args);
    case "library:analysis-estimates":
      exactRange(args, 1, 2); assertShortString(args[0]); if (args[1] !== undefined) validateAnalysisScope(args[1]); return;
    case "library:set-directory-included":
      exact(args, 3); assertShortString(args[0]); assertRelativeDirectory(args[1]); if (typeof args[2] !== "boolean") fail(); return;
    case "library:set-directory-visible":
      exact(args, 4); assertShortString(args[0]); assertRelativeDirectory(args[1]);
      assertEnum(args[2], new Set(["subtree", "direct"])); if (typeof args[3] !== "boolean") fail(); return;
    case "library:set-directory-hidden":
      exact(args, 3); assertShortString(args[0]); assertRelativeDirectory(args[1]); if (typeof args[2] !== "boolean") fail(); return;
    case "library:remove-directory-assets":
      exact(args, 3); assertShortString(args[0]); assertRelativeDirectory(args[1]); if (typeof args[2] !== "boolean") fail(); return;
    case "library:analyze":
      exactRange(args, 2, 3); assertShortString(args[0]); assertEnum(args[1], new Set(["simple", "medium", "detailed", "precise", "probe"]));
      if (args[2] !== undefined) validateAnalysisScope(args[2]); return;
    case "library:bulk-analysis-plan":
      exact(args, 1);
      if (args[0] !== "") assertEnum(args[0], new Set(["video", "image"]));
      return;
    case "library:thumbnails":
      exact(args, 1);
      if (!Array.isArray(args[0]) || args[0].length > 100) fail();
      for (const item of args[0]) assertShortString(item);
      return;
    case "library:list-assets": exact(args, 1); validateAssetListRequest(args[0]); return;
    case "library:update-description":
      exact(args, 2); assertShortString(args[0]);
      if (typeof args[1] !== "string" || args[1].length > 1_000_000 || args[1].includes("\0")) fail();
      return;
    case "library:add-segment":
      exact(args, 3); assertShortString(args[0]); validateAnalysisScope(args[1]);
      if (typeof args[2] !== "string" || !args[2].trim() || args[2].length > 4000 || args[2].includes("\0")) fail();
      return;
    case "library:web-probe":
      exact(args, 1); assertWebUrl(args[0]); return;
    case "library:web-acquire":
      exact(args, 1); validateWebAcquireRequest(args[0]); return;
    case "editorial:inspect-checkpoint":
      exactRange(args, 1, 2); assertAbsolutePath(args[0]);
      if (args[1] !== undefined) validateEditorialSources(args[1]);
      return;
    case "editorial:find-checkpoint":
      exact(args, 1); validateEditorialSources(args[0]); return;
    case "editorial:remove-checkpoint": return absolutePathArg(args);
    case "editorial:remember-game": return shortStringArg(args);
    case "run:start": exact(args, 1); validateRunRequest(args[0]); return;
    default: throw new Error(`No IPC validation policy for ${channel}`);
  }
}

function validateAssetListRequest(value: unknown): void {
  assertPlainObject(value);
  const request = value as Record<string, unknown>;
  const allowed = new Set(["query", "rootId", "relativeDirectory", "mediaKind", "availability", "limit", "offset"]);
  if (Object.keys(request).some((key) => !allowed.has(key))) fail();
  for (const key of ["query", "rootId", "relativeDirectory"] as const) {
    if (request[key] !== undefined && request[key] !== "") assertShortString(request[key]);
  }
  if (request.mediaKind !== undefined) assertEnum(request.mediaKind, new Set(["video", "image"]));
  if (request.availability !== undefined) {
    assertEnum(request.availability, new Set(["active", "missing", "incompatible"]));
  }
  for (const key of ["limit", "offset"] as const) {
    if (request[key] !== undefined && (!Number.isSafeInteger(request[key]) || Number(request[key]) < 0)) fail();
  }
  if (request.limit !== undefined && Number(request.limit) > 200) fail();
}

function validateWebAcquireRequest(value: unknown): void {
  assertPlainObject(value);
  const allowed = new Set(["sourceUrl", "description", "rightsConfirmed", "creator", "licenseText", "windowStartSec"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) fail();
  assertWebUrl(value.sourceUrl);
  if (typeof value.description !== "string" || !value.description.trim() || value.description.length > 100_000 || value.description.includes("\0")) fail();
  if (value.rightsConfirmed !== true) fail();
  for (const key of ["creator", "licenseText"]) {
    if (value[key] !== undefined && (typeof value[key] !== "string" || String(value[key]).length > 10_000 || String(value[key]).includes("\0"))) fail();
  }
  if (value.windowStartSec !== undefined && (typeof value.windowStartSec !== "number" || !Number.isFinite(value.windowStartSec) || value.windowStartSec < 0)) fail();
}

type GuardedWebContents = {
  setWindowOpenHandler(handler: () => { action: "deny" }): void;
  on(event: "will-navigate", listener: (event: { preventDefault(): void }, url: string) => void): void;
};

export function installNavigationGuards(window: { webContents: GuardedWebContents }): void {
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event: { preventDefault(): void }, _url: string) => {
    // The application is a single page and never needs top-level navigation.
    event.preventDefault();
  });
}

export function contentSecurityPolicy(packaged: boolean): string {
  const scripts = packaged ? "script-src 'self'" : "script-src 'self' 'unsafe-inline'";
  const connections = packaged ? "connect-src 'self'" : "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*";
  return `default-src 'self'; ${scripts}; style-src 'self' 'unsafe-inline'; img-src 'self' data: subutl-media:; media-src 'self' subutl-media: blob:; ${connections}; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`;
}

function validateRunRequest(value: unknown): void {
  assertPlainObject(value);
  const request = value as Record<string, unknown>;
  const allowed = new Set(["workflow", "inputPath", "outputPath", "configPath", "envFile", "audioTrack", "sidecarDir", "profile", "sidecarsEnabled", "cutSilenceEncoderPreset", "silencePreviewHeight", "silencePreviewFps", "editorialProject", "editorialCheckpoint", "editorialCheckpointSources", "editorialRestartFrom", "editorialExtend"]);
  if (Object.keys(request).some((key) => !allowed.has(key))) fail();
  assertEnum(request.workflow, workflows);
  for (const key of ["inputPath", "outputPath", "configPath", "envFile"]) assertAbsolutePath(request[key]);
  if (request.sidecarDir !== undefined) assertAbsolutePath(request.sidecarDir);
  if (request.audioTrack !== undefined && (!Number.isSafeInteger(request.audioTrack) || Number(request.audioTrack) < 0)) fail();
  if (typeof request.profile !== "boolean" || typeof request.sidecarsEnabled !== "boolean") fail();
  assertEnum(request.cutSilenceEncoderPreset, new Set(["unconfigured", "hevc-amf-cqp21", "hevc-nvenc-qp21", "hevc-qsv-q21", "libx265-crf21"]));
  if (![240, 360, 480, 720].includes(Number(request.silencePreviewHeight)) || ![4, 8, 12, 24].includes(Number(request.silencePreviewFps))) fail();
  if (request.editorialProject !== undefined) validateEditorialProjectRequest(request.workflow, request.editorialProject);
  if (request.editorialCheckpoint !== undefined) {
    if (request.workflow !== "hosted-long-stream" || (request.editorialProject !== undefined && request.editorialExtend !== true)) fail();
    assertAbsolutePath(request.editorialCheckpoint);
  }
  if (request.editorialRestartFrom !== undefined) {
    if (request.editorialCheckpoint === undefined) fail();
    assertEnum(request.editorialRestartFrom, new Set(["compatible", "source_probe", "transcription", "visual_learning", "semantic_spans", "local_reconciliation", "global_reconciliation"]));
  }
  if (request.editorialCheckpointSources !== undefined) {
    if (request.editorialCheckpoint === undefined) fail();
    validateEditorialSources(request.editorialCheckpointSources);
  }
  if (request.editorialExtend !== undefined) {
    if (request.editorialExtend !== true || request.editorialCheckpoint === undefined || request.editorialProject === undefined) fail();
  }
}
function validateEditorialProjectRequest(workflow: unknown, value: unknown): void {
  if (workflow !== "hosted-long-stream") fail();
  assertPlainObject(value);
  const allowed = new Set(["sources", "titleOrGame", "objective", "targetDurationMinSeconds", "targetDurationMaxSeconds", "mustKeepNotes", "deEmphasizeNotes", "subtitleMode", "outputLocale"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) fail();
  const totalSeconds = validateEditorialSources(value.sources);
  for (const key of ["titleOrGame", "objective"] as const) {
    if (typeof value[key] !== "string" || !value[key].trim() || value[key].length > 20_000 || value[key].includes("\0")) fail();
  }
  const minimum = value.targetDurationMinSeconds;
  const maximum = value.targetDurationMaxSeconds;
  if (typeof minimum !== "number" || !Number.isFinite(minimum) || minimum <= 0
    || typeof maximum !== "number" || !Number.isFinite(maximum) || maximum < minimum || maximum > totalSeconds + 1) fail();
  for (const key of ["mustKeepNotes", "deEmphasizeNotes"] as const) {
    if (!Array.isArray(value[key]) || value[key].length > 1000) fail();
    for (const note of value[key]) {
      if (typeof note !== "string" || !note.trim() || note.length > 4000 || note.includes("\0")) fail();
    }
  }
  if (value.subtitleMode !== undefined) assertEnum(value.subtitleMode, new Set(["full", "emphasis"]));
  if (value.outputLocale !== undefined) assertEnum(value.outputLocale, new Set(["en", "ja"]));
}
function validateEditorialSources(value: unknown): number {
  if (!Array.isArray(value) || value.length < 1 || value.length > 1000) fail();
  let totalSeconds = 0;
  const mediaPaths = new Set<string>();
  for (const source of value) {
    assertPlainObject(source);
    const sourceFields = new Set(["path", "durationSeconds", "mode", "audioPath", "visualPath", "audioDurationSeconds", "visualDurationSeconds", "width", "height", "audioWidth", "audioHeight", "frameRate", "audioFrameRate", "pairingBasis", "roleConfirmed"]);
    if (Object.keys(source).some((key) => !sourceFields.has(key)) || Object.keys(source).length !== sourceFields.size) fail();
    assertEnum(source.mode, new Set(["single", "paired"]));
    assertEnum(source.pairingBasis, new Set(["single", "filename", "resolution", "manual"]));
    for (const key of ["path", "audioPath", "visualPath"] as const) assertAbsolutePath(source[key]);
    for (const key of ["durationSeconds", "audioDurationSeconds", "visualDurationSeconds"] as const) {
      if (typeof source[key] !== "number" || !Number.isFinite(source[key]) || Number(source[key]) <= 0) fail();
    }
    for (const key of ["width", "height", "audioWidth", "audioHeight"] as const) {
      if (source[key] !== null && (!Number.isSafeInteger(source[key]) || Number(source[key]) <= 0)) fail();
    }
    if (source.frameRate !== null && (typeof source.frameRate !== "number" || !Number.isFinite(source.frameRate) || source.frameRate <= 0)) fail();
    if (source.audioFrameRate !== null && (typeof source.audioFrameRate !== "number" || !Number.isFinite(source.audioFrameRate) || source.audioFrameRate <= 0)) fail();
    if (typeof source.roleConfirmed !== "boolean" || !source.roleConfirmed) fail();
    if (source.path !== source.visualPath || Math.abs(Number(source.durationSeconds) - Number(source.visualDurationSeconds)) > 0.001) fail();
    if (source.mode === "single") {
      if (source.audioPath !== source.visualPath || source.pairingBasis !== "single") fail();
    } else {
      if (source.audioPath === source.visualPath || source.pairingBasis === "single" || source.frameRate === null) fail();
      if (Math.abs(Number(source.audioDurationSeconds) - Number(source.visualDurationSeconds)) > 10 / Number(source.frameRate) + 0.001) fail();
    }
    for (const mediaPath of new Set([String(source.audioPath), String(source.visualPath)])) {
      const normalized = mediaPath.toLocaleLowerCase();
      if (mediaPaths.has(normalized)) fail();
      mediaPaths.add(normalized);
    }
    totalSeconds += Number(source.durationSeconds);
  }
  return totalSeconds;
}
function validateSilenceReview(args: unknown[]): void {
  exact(args, 3); assertShortString(args[0]); assertShortString(args[1]);
  if (!Array.isArray(args[2]) || args[2].length > 10_000) fail();
  for (const item of args[2]) {
    assertPlainObject(item); assertShortString(item.candidateId);
    assertEnum(item.decision, new Set(["accept_cut", "reject_cut", "mark_and_reject"]));
  }
}
function validateBrollReview(args: unknown[]): void {
  exact(args, 3); assertShortString(args[0]); assertShortString(args[1]);
  if (!Array.isArray(args[2]) || args[2].length > 10_000) fail();
  for (const item of args[2]) {
    assertPlainObject(item); assertShortString(item.candidateId);
    assertEnum(item.decision, new Set(["describe", "use_library", "reject"]));
    if (item.decision === "describe" && (typeof item.description !== "string" || !item.description.trim() || item.description.length > 4000)) fail();
  }
}
function validateAnalysisScope(value: unknown): void {
  assertPlainObject(value);
  const scope = value as Record<string, unknown>;
  if (Object.keys(scope).some((key) => key !== "startMs" && key !== "endMs")) fail();
  if (!Number.isSafeInteger(scope.startMs) || !Number.isSafeInteger(scope.endMs)
    || Number(scope.startMs) < 0 || Number(scope.endMs) <= Number(scope.startMs)) fail();
}
function absolutePathArg(args: unknown[]): void { exact(args, 1); assertAbsolutePath(args[0]); }
function shortStringArg(args: unknown[]): void { exact(args, 1); assertShortString(args[0]); }
function optionalAbsolutePath(args: unknown[]): void { exactRange(args, 0, 1); if (args[0] !== undefined) assertAbsolutePath(args[0]); }
function objectArg(args: unknown[]): void { exact(args, 1); assertPlainObject(args[0]); }
function enumArg(args: unknown[], values: Set<string>): void { exact(args, 1); assertEnum(args[0], values); }
function exact(args: unknown[], count: number): void { if (args.length !== count) fail(); }
function exactRange(args: unknown[], min: number, max: number): void { if (args.length < min || args.length > max) fail(); }
function assertEnum(value: unknown, values: Set<string>): void { if (typeof value !== "string" || !values.has(value)) fail(); }
function assertShortString(value: unknown): void { if (typeof value !== "string" || !value || value.length > 4096 || value.includes("\0")) fail(); }
function assertRelativeDirectory(value: unknown): void { if (typeof value !== "string" || value.length > 4096 || value.includes("\0")) fail(); }
function assertAbsolutePath(value: unknown): void { assertShortString(value); if (!path.isAbsolute(value as string)) fail(); }
function assertWebUrl(value: unknown): void {
  assertShortString(value);
  let parsed: URL;
  try { parsed = new URL(value as string); } catch { fail(); }
  if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password) fail();
}
function assertPlainObject(value: unknown): asserts value is Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype) fail(); }
function fail(): never { throw new TypeError("Invalid IPC payload"); }
