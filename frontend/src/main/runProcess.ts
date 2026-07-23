import { BrowserWindow } from "electron";
import { spawn, spawnSync, ChildProcessWithoutNullStreams } from "node:child_process";
import crypto from "node:crypto";
import type { RunEvent, RunRequest, SilenceCutDecision, SilenceCutCandidate } from "../renderer/lib/types";
import { buildRunCommand } from "./python";
import type { RuntimePaths } from "./paths";

type ActiveRun = {
  runId: string;
  process: ChildProcessWithoutNullStreams;
  startedAtMs: number;
  cancelled: boolean;
  forceTimer?: NodeJS.Timeout;
  reviewId?: string;
  reviewCandidates?: Set<string>;
};

let activeRun: ActiveRun | null = null;
const FORCE_DELAY_MS = 3000;
const pendingForceTimers = new Map<number, NodeJS.Timeout>();

export function startRun(window: BrowserWindow, paths: RuntimePaths, pythonPath: string, request: RunRequest, callbacks?: { onControlEvent?(event: RunEvent): void; onFinish?(runId: string): void }): { runId: string } {
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
    callbacks?.onFinish?.(runId);
  };
  emit(window, { type: "started", runId, commandPreview: command.preview, startedAt: new Date(startedAtMs).toISOString() });

  let stdoutBuffer = "";
  child.stdout.on("data", (data: Buffer) => {
    stdoutBuffer += data.toString("utf8");
    let newline = stdoutBuffer.indexOf("\n");
    while (newline >= 0) {
      const line = stdoutBuffer.slice(0, newline + 1);
      stdoutBuffer = stdoutBuffer.slice(newline + 1);
      handleStdoutLine(window, runId, line, callbacks?.onControlEvent);
      newline = stdoutBuffer.indexOf("\n");
    }
  });
  child.stderr.on("data", (data: Buffer) => emit(window, { type: "stderr", runId, text: data.toString("utf8") }));
  child.on("error", (error) => {
    emit(window, { type: "error", runId, message: `Could not start workflow process: ${error.message}` });
    finish(null, null);
  });
  child.on("close", (code, signal) => {
    if (stdoutBuffer) handleStdoutLine(window, runId, stdoutBuffer, callbacks?.onControlEvent);
    finish(code, signal);
  });
  return { runId };
}

export function submitSilenceReview(runId: string, reviewId: string, decisions: Array<{ candidateId: string; decision: SilenceCutDecision }>): void {
  if (!activeRun || activeRun.runId !== runId || activeRun.reviewId !== reviewId || !activeRun.reviewCandidates) {
    throw new Error("No matching Cut silence review is active");
  }
  const valid = new Set(["accept_cut", "reject_cut", "mark_and_reject"]);
  const seen = new Set<string>();
  for (const item of decisions) {
    if (!activeRun.reviewCandidates.has(item.candidateId) || !valid.has(item.decision) || seen.has(item.candidateId)) {
      throw new Error("Invalid Cut silence review decisions");
    }
    seen.add(item.candidateId);
  }
  if (seen.size !== activeRun.reviewCandidates.size) throw new Error("Every Cut silence candidate requires a decision");
  activeRun.process.stdin.write(`${JSON.stringify({ type: "silence-review-result", reviewId, decisions })}\n`);
  activeRun.reviewId = undefined;
  activeRun.reviewCandidates = undefined;
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

function handleStdoutLine(window: BrowserWindow, runId: string, line: string, onControlEvent?: (event: RunEvent) => void): void {
  const prefix = "@@SUBUTL_EVENT@@";
  if (!line.startsWith(prefix)) {
    emit(window, { type: "stdout", runId, text: line });
    return;
  }
  try {
    const value = JSON.parse(line.slice(prefix.length)) as Record<string, unknown>;
    let event: RunEvent;
    if (value.type === "silence-candidates" || value.type === "silence-review-required") {
      const candidates = validateCandidates(value.candidates);
      if (value.type === "silence-candidates") {
        if (!["local", "hosted", "local-long-stream", "hosted-long-stream"].includes(String(value.workflow))) throw new Error();
        event = { type: "silence-candidates", runId, workflow: value.workflow as RunRequest["workflow"], candidates };
      } else {
        if (typeof value.reviewId !== "string" || !value.reviewId) throw new Error();
        event = { type: "silence-review-required", runId, reviewId: value.reviewId, candidates };
        if (!activeRun || activeRun.runId !== runId) throw new Error();
        activeRun.reviewId = value.reviewId;
        activeRun.reviewCandidates = new Set(candidates.map((candidate) => candidate.id));
      }
    } else if (value.type === "silence-cut-output") {
      if (typeof value.path !== "string" || !value.path) throw new Error();
      event = { type: "silence-cut-output", runId, path: value.path };
    } else {
      throw new Error();
    }
    onControlEvent?.(event);
    emit(window, event);
  } catch {
    emit(window, { type: "stderr", runId, text: "Invalid Cut silence control event was rejected.\n" });
  }
}

function validateCandidates(value: unknown): SilenceCutCandidate[] {
  if (!Array.isArray(value) || value.length > 10_000) throw new Error();
  const ids = new Set<string>();
  return value.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error();
    const candidate = item as Record<string, unknown>;
    const numbers = [candidate.silenceStart, candidate.silenceEnd, candidate.cutStart, candidate.cutEnd, candidate.cutDuration];
    if (typeof candidate.id !== "string" || !candidate.id || ids.has(candidate.id) || numbers.some((number) => typeof number !== "number" || !Number.isFinite(number))) throw new Error();
    if (Number(candidate.cutStart) < 0 || Number(candidate.cutEnd) <= Number(candidate.cutStart) || Number(candidate.cutDuration) <= 0) throw new Error();
    ids.add(candidate.id);
    return candidate as unknown as SilenceCutCandidate;
  });
}
