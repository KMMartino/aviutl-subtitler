import { BrowserWindow } from "electron";
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import crypto from "node:crypto";
import type { RunEvent, RunRequest } from "../renderer/lib/types";
import { buildRunCommand, projectRoot } from "./python";

let activeRun: { runId: string; process: ChildProcessWithoutNullStreams; startedAtMs: number; cancelled: boolean } | null = null;

export function startRun(window: BrowserWindow, pythonPath: string, request: RunRequest): { runId: string } {
  if (activeRun) {
    throw new Error("A run is already active");
  }
  const runId = crypto.randomUUID();
  const startedAtMs = Date.now();
  const command = buildRunCommand(projectRoot(), pythonPath, request);
  const child = spawn(command.command, command.args, {
    cwd: projectRoot(),
    windowsHide: true
  });
  activeRun = { runId, process: child, startedAtMs, cancelled: false };
  emit(window, { type: "started", runId, commandPreview: command.preview, startedAt: new Date(startedAtMs).toISOString() });

  child.stdout.on("data", (data: Buffer) => emit(window, { type: "stdout", runId, text: data.toString("utf8") }));
  child.stderr.on("data", (data: Buffer) => emit(window, { type: "stderr", runId, text: data.toString("utf8") }));
  child.on("error", (error) => emit(window, { type: "error", runId, message: error.message }));
  child.on("exit", (code, signal) => {
    const elapsedMs = Date.now() - startedAtMs;
    emit(window, { type: "exit", runId, code, signal, elapsedMs, cancelled: activeRun?.cancelled ?? false });
    activeRun = null;
  });
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
