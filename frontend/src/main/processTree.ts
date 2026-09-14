import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from "node:child_process";

const FORCE_DELAY_MS = 3000;
const pendingForceTimers = new Map<number, NodeJS.Timeout>();

export function forgetProcessTree(pid: number): void {
  const timer = pendingForceTimers.get(pid);
  if (timer) clearTimeout(timer);
  pendingForceTimers.delete(pid);
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

export function shutdownProcessTrees(activePid?: number): void {
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

