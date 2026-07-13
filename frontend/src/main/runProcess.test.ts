import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  spawn: vi.fn(),
  spawnSync: vi.fn(),
}));

vi.mock("node:child_process", async (importOriginal) => {
  const original = await importOriginal<typeof import("node:child_process")>();
  return { ...original, spawn: mocks.spawn, spawnSync: mocks.spawnSync };
});

vi.mock("./python", () => ({
  buildRunCommand: () => ({
    command: "python.exe",
    args: ["fixture.py"],
    cwd: "C:/fixture",
    env: {},
    preview: "python.exe fixture.py",
  }),
}));

import { cancelRun, shutdownActiveRun, startRun, terminateProcessTree } from "./runProcess";

class FixtureChild extends EventEmitter {
  pid = 43210;
  stdout = new PassThrough();
  stderr = new PassThrough();
  kill = vi.fn(() => true);
}

function fixtureWindow(send = vi.fn(), destroyed = false) {
  return {
    isDestroyed: () => destroyed,
    webContents: {
      isDestroyed: () => destroyed,
      send,
    },
  };
}

const originalPlatform = process.platform;

function setPlatform(value: NodeJS.Platform): void {
  Object.defineProperty(process, "platform", { value, configurable: true });
}

describe("workflow process lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.spawn.mockReset();
    mocks.spawnSync.mockReset();
    setPlatform("win32");
  });

  afterEach(() => {
    shutdownActiveRun();
    vi.clearAllTimers();
    vi.useRealTimers();
    setPlatform(originalPlatform);
  });

  it("gracefully terminates a Windows tree before forcing it", () => {
    const taskkill = new EventEmitter();
    mocks.spawn.mockReturnValue(taskkill);
    const child = new FixtureChild();
    terminateProcessTree(child as never, false);
    expect(mocks.spawn).toHaveBeenCalledWith(
      "taskkill",
      ["/PID", "43210", "/T"],
      expect.objectContaining({ windowsHide: true }),
    );
    expect(mocks.spawnSync).not.toHaveBeenCalled();
    vi.advanceTimersByTime(3000);
    expect(mocks.spawnSync).toHaveBeenCalledWith(
      "taskkill",
      ["/PID", "43210", "/T", "/F"],
      expect.objectContaining({ timeout: 5000 }),
    );
  });

  it("uses forced tree termination during app shutdown", () => {
    const child = new FixtureChild();
    mocks.spawn.mockReturnValue(child);
    const window = fixtureWindow();
    startRun(window as never, {} as never, "python.exe", {} as never);
    shutdownActiveRun();
    expect(mocks.spawnSync).toHaveBeenCalledWith(
      "taskkill",
      ["/PID", "43210", "/T", "/F"],
      expect.objectContaining({ timeout: 5000 }),
    );
    child.emit("close", null, null);
  });

  it("cancellation targets the process tree and closes streams on completion", () => {
    const child = new FixtureChild();
    const taskkill = new EventEmitter();
    mocks.spawn.mockImplementation((command: string) => (command === "taskkill" ? taskkill : child));
    const send = vi.fn();
    const { runId } = startRun(fixtureWindow(send) as never, {} as never, "python.exe", {} as never);
    cancelRun(runId);
    expect(mocks.spawn).toHaveBeenLastCalledWith(
      "taskkill",
      ["/PID", "43210", "/T"],
      expect.any(Object),
    );
    child.emit("close", 1, null);
    expect(child.stdout.destroyed).toBe(true);
    expect(child.stderr.destroyed).toBe(true);
    expect(send).toHaveBeenLastCalledWith(
      "run:event",
      expect.objectContaining({ type: "exit", runId, cancelled: true }),
    );
    vi.advanceTimersByTime(3000);
    expect(mocks.spawnSync).toHaveBeenCalledWith(
      "taskkill",
      ["/PID", "43210", "/T", "/F"],
      expect.objectContaining({ timeout: 5000 }),
    );
  });

  it("app shutdown forces a pending tree even after its Python parent closed", () => {
    const child = new FixtureChild();
    const taskkill = new EventEmitter();
    mocks.spawn.mockImplementation((command: string) => (command === "taskkill" ? taskkill : child));
    const { runId } = startRun(fixtureWindow() as never, {} as never, "python.exe", {} as never);
    cancelRun(runId);
    child.emit("close", 1, null);
    shutdownActiveRun();
    expect(mocks.spawnSync).toHaveBeenCalledWith(
      "taskkill",
      ["/PID", "43210", "/T", "/F"],
      expect.objectContaining({ timeout: 5000 }),
    );
  });

  it("normalizes spawn errors and closes both output streams", () => {
    const child = new FixtureChild();
    mocks.spawn.mockReturnValue(child);
    const send = vi.fn();
    const { runId } = startRun(fixtureWindow(send) as never, {} as never, "python.exe", {} as never);
    child.emit("error", new Error("fixture spawn failure"));
    expect(send).toHaveBeenCalledWith("run:event", {
      type: "error",
      runId,
      message: "Could not start workflow process: fixture spawn failure",
    });
    expect(child.stdout.destroyed).toBe(true);
    expect(child.stderr.destroyed).toBe(true);
    expect(send).toHaveBeenLastCalledWith(
      "run:event",
      expect.objectContaining({ type: "exit", runId, code: null, signal: null }),
    );
  });

  it("ignores child error and close events after the window is destroyed", () => {
    const child = new FixtureChild();
    mocks.spawn.mockReturnValue(child);
    const send = vi.fn();
    const window = fixtureWindow(send, true);
    startRun(window as never, {} as never, "python.exe", {} as never);
    expect(() => child.emit("error", new Error("late shutdown error"))).not.toThrow();
    expect(() => child.emit("close", 1, null)).not.toThrow();
    expect(send).not.toHaveBeenCalled();
    expect(child.stdout.destroyed).toBe(true);
    expect(child.stderr.destroyed).toBe(true);
  });
});
