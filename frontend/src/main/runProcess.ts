import { BrowserWindow } from "electron";
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import crypto from "node:crypto";
import type { RunEvent, RunRequest } from "../renderer/lib/types";
import { buildRunCommand } from "./python";
import type { RuntimePaths } from "./paths";

let activeRun: { runId: string; process: ChildProcessWithoutNullStreams; startedAtMs: number; cancelled: boolean } | null = null;

export function startRun(window: BrowserWindow, paths: RuntimePaths, pythonPath: string, request: RunRequest): { runId: string } {
  if (activeRun) {
    throw new Error("A run is already active");
  }
  const runId = crypto.randomUUID();
  const startedAtMs = Date.now();
  const command = buildRunCommand(paths, pythonPath, request);
  const child = spawn(command.command, command.args, {
    cwd: command.cwd,
    env: command.env,
    windowsHide: true,
  });
  activeRun = { runId, process: child, startedAtMs, cancelled: false };
  let finished = false;
  const finish = (code: number | null, signal: string | null) => {
    if (finished) return;
    finished = true;
    const elapsedMs = Date.now() - startedAtMs;
    emit(window, { type: "exit", runId, code, signal, elapsedMs, cancelled: activeRun?.cancelled ?? false });
    activeRun = null;
  };
  emit(window, { type: "started", runId, commandPreview: command.preview, startedAt: new Date(startedAtMs).toISOString() });

  child.stdout.on("data", (data: Buffer) => emit(window, { type: "stdout", runId, text: data.toString("utf8") }));
  child.stderr.on("data", (data: Buffer) => emit(window, { type: "stderr", runId, text: data.toString("utf8") }));
  child.on("error", (error) => {
    emit(window, { type: "error", runId, message: error.message });
    finish(null, null);
  });
  child.on("close", finish);
  return { runId };
}

export function cancelRun(runId: string): void {
  if (!activeRun || activeRun.runId !== runId) return;
  activeRun.cancelled = true;
  activeRun.process.kill();
}

function emit(window: BrowserWindow, event: RunEvent): void {
  window.webContents.send("run:event", event);
}
