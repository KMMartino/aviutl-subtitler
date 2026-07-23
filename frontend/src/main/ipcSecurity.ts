import path from "node:path";
import type { IpcMainInvokeEvent } from "electron";

const workflows = new Set(["local", "hosted", "local-long-stream", "hosted-long-stream"]);
const backends = new Set(["vulkan", "cuda-12"]);
const noArgs = new Set([
  "dialog:file", "dialog:directory", "dialog:executable", "state:get", "state:reset",
  "local-models:list", "local-models:hf-downloader-status", "local-models:install-hf-downloader", "llama:list-backends",
  "llama:check-latest", "glossary:read", "glossary:import", "runtime:setup-status", "runtime:create-managed-python",
  "runtime:install-python-requirements", "runtime:delete-managed-python", "runtime:download-ffmpeg", "runtime:delete-ffmpeg",
  "runtime:download-alignment", "runtime:delete-alignment",
  "silence:probe-encoders",
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
    case "dialog:input-file": case "dialog:output-file": return optionalAbsolutePath(args);
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
    case "silence:proxy": exact(args, 3); assertShortString(args[0]); assertShortString(args[1]); assertEnum(args[2], new Set(["original", "seam"])); return;
    case "silence:prefetch": exact(args, 2); assertShortString(args[0]); if (!Array.isArray(args[1]) || args[1].length > 2) fail(); for (const item of args[1]) assertShortString(item); return;
    case "run:submit-silence-review": return validateSilenceReview(args);
    case "run:start": exact(args, 1); validateRunRequest(args[0]); return;
    default: throw new Error(`No IPC validation policy for ${channel}`);
  }
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
  return `default-src 'self'; ${scripts}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' subutl-media: blob:; ${connections}; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`;
}

function validateRunRequest(value: unknown): void {
  assertPlainObject(value);
  const request = value as Record<string, unknown>;
  const allowed = new Set(["workflow", "inputPath", "outputPath", "configPath", "envFile", "audioTrack", "sidecarDir", "profile", "sidecarsEnabled", "cutSilenceEncoderPreset", "silencePreviewHeight", "silencePreviewFps"]);
  if (Object.keys(request).some((key) => !allowed.has(key))) fail();
  assertEnum(request.workflow, workflows);
  for (const key of ["inputPath", "outputPath", "configPath", "envFile"]) assertAbsolutePath(request[key]);
  if (request.sidecarDir !== undefined) assertAbsolutePath(request.sidecarDir);
  if (request.audioTrack !== undefined && (!Number.isSafeInteger(request.audioTrack) || Number(request.audioTrack) < 0)) fail();
  if (typeof request.profile !== "boolean" || typeof request.sidecarsEnabled !== "boolean") fail();
  assertEnum(request.cutSilenceEncoderPreset, new Set(["unconfigured", "hevc-amf-cqp21", "hevc-nvenc-qp21", "hevc-qsv-q21", "libx265-crf21"]));
  if (![240, 360, 480, 720].includes(Number(request.silencePreviewHeight)) || ![4, 8, 12, 24].includes(Number(request.silencePreviewFps))) fail();
}
function validateSilenceReview(args: unknown[]): void {
  exact(args, 3); assertShortString(args[0]); assertShortString(args[1]);
  if (!Array.isArray(args[2]) || args[2].length > 10_000) fail();
  for (const item of args[2]) {
    assertPlainObject(item); assertShortString(item.candidateId);
    assertEnum(item.decision, new Set(["accept_cut", "reject_cut", "mark_and_reject"]));
  }
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
function assertAbsolutePath(value: unknown): void { assertShortString(value); if (!path.isAbsolute(value as string)) fail(); }
function assertPlainObject(value: unknown): asserts value is Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype) fail(); }
function fail(): never { throw new TypeError("Invalid IPC payload"); }
