import { BrowserWindow } from "electron";
import { spawn, spawnSync, ChildProcessWithoutNullStreams } from "node:child_process";
import crypto from "node:crypto";
import type { RunEvent, RunRequest } from "../renderer/lib/types";
import { buildRunCommand } from "./python";
import type { RuntimePaths } from "./paths";

type ActiveRun = {
  runId: string;
  process: ChildProcessWithoutNullStreams;
  startedAtMs: number;
  cancelled: boolean;
  forceTimer?: NodeJS.Timeout;
};

let activeRun: ActiveRun | null = null;
const FORCE_DELAY_MS = 3000;
const pendingForceTimers = new Map<number, NodeJS.Timeout>();

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
    detached: process.platform !== "win32",
  });
  activeRun = { runId, process: child, startedAtMs, cancelled: false };
  let finished = false;
  const finish = (code: number | null, signal: string | null) => {
    if (finished) return;
    finished = true;
    const cancelled = activeRun?.cancelled ?? false;
    // Keep the forced fallback alive after a cancelled parent exits: one of its
    // FFmpeg/llama-server descendants may have resisted graceful termination.
    if (activeRun?.forceTimer && !cancelled) {
      clearTimeout(activeRun.forceTimer);
      if (child.pid) pendingForceTimers.delete(child.pid);
    }
    child.stdout.destroy();
    child.stderr.destroy();
    const elapsedMs = Date.now() - startedAtMs;
    emit(window, { type: "exit", runId, code, signal, elapsedMs, cancelled });
    activeRun = null;
  };
  emit(window, { type: "started", runId, commandPreview: command.preview, startedAt: new Date(startedAtMs).toISOString() });

  child.stdout.on("data", (data: Buffer) => emit(window, { type: "stdout", runId, text: data.toString("utf8") }));
  child.stderr.on("data", (data: Buffer) => emit(window, { type: "stderr", runId, text: data.toString("utf8") }));
  child.on("error", (error) => {
    emit(window, { type: "error", runId, message: `Could not start workflow process: ${error.message}` });
    finish(null, null);
  });
  child.on("close", finish);
  return { runId };
}

export function cancelRun(runId: string): void {
  if (!activeRun || activeRun.runId !== runId) return;
  activeRun.cancelled = true;
  activeRun.forceTimer = terminateProcessTree(activeRun.process, false);
}

/** Stop the whole workflow tree. On Windows this includes Python's FFmpeg and llama-server descendants. */
export function terminateProcessTree(child: Pick<ChildProcessWithoutNullStreams, "pid" | "kill">, immediate: boolean): NodeJS.Timeout | undefined {
  if (!child.pid) {
    child.kill();
    return undefined;
  }
  if (process.platform === "win32") {
    if (immediate) {
      forceWindowsTree(child.pid);
      return undefined;
    }
    const graceful = spawn("taskkill", ["/PID", String(child.pid), "/T"], {
      windowsHide: true,
      stdio: "ignore",
    });
    graceful.on("error", () => undefined);
    const timer = setTimeout(() => {
      forceWindowsTree(child.pid!);
      pendingForceTimers.delete(child.pid!);
    }, FORCE_DELAY_MS);
    timer.unref();
    pendingForceTimers.set(child.pid, timer);
    return timer;
  }
  try {
    process.kill(-child.pid, immediate ? "SIGKILL" : "SIGTERM");
  } catch {
    child.kill(immediate ? "SIGKILL" : "SIGTERM");
  }
  if (immediate) return undefined;
  const timer = setTimeout(() => {
    try {
      process.kill(-child.pid!, "SIGKILL");
    } catch {
      // The process group already exited.
    }
    pendingForceTimers.delete(child.pid!);
  }, FORCE_DELAY_MS);
  timer.unref();
  pendingForceTimers.set(child.pid, timer);
  return timer;
}

export function shutdownActiveRun(): void {
  const activePid = activeRun?.process.pid;
  if (activeRun) {
    activeRun.cancelled = true;
    if (activeRun.forceTimer) clearTimeout(activeRun.forceTimer);
    terminateProcessTree(activeRun.process, true);
  }
  for (const [pid, timer] of pendingForceTimers) {
    clearTimeout(timer);
    if (pid === activePid) continue;
    if (process.platform === "win32") {
      forceWindowsTree(pid);
    } else {
      try {
        process.kill(-pid, "SIGKILL");
      } catch {
        // The process group already exited.
      }
    }
  }
  pendingForceTimers.clear();
}

function forceWindowsTree(pid: number): void {
  spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
    windowsHide: true,
    stdio: "ignore",
    timeout: 5000,
  });
}

function emit(window: BrowserWindow, event: RunEvent): void {
  try {
    if (window.isDestroyed() || window.webContents.isDestroyed()) return;
    window.webContents.send("run:event", event);
  } catch {
    // The window can be destroyed between the checks and send while a child
    // process is closing during application shutdown.
  }
}
