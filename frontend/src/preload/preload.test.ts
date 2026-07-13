import { beforeEach, describe, expect, it, vi } from "vitest";

const electron = vi.hoisted(() => ({
  api: undefined as Record<string, unknown> | undefined,
  exposeInMainWorld: vi.fn((_name: string, api: Record<string, unknown>) => {
    electron.api = api;
  }),
  invoke: vi.fn(),
  on: vi.fn(),
  removeListener: vi.fn(),
  getPathForFile: vi.fn(),
}));

vi.mock("electron", () => ({
  contextBridge: { exposeInMainWorld: electron.exposeInMainWorld },
  ipcRenderer: {
    invoke: electron.invoke,
    on: electron.on,
    removeListener: electron.removeListener,
  },
  webUtils: { getPathForFile: electron.getPathForFile },
}));

await import("./preload");

describe("preload bridge contract", () => {
  beforeEach(() => {
    electron.invoke.mockReset();
    electron.on.mockReset();
    electron.removeListener.mockReset();
    electron.getPathForFile.mockReset();
  });

  it("exposes the bridge once without exposing Electron primitives", () => {
    expect(electron.exposeInMainWorld).toHaveBeenCalledOnce();
    expect(electron.exposeInMainWorld).toHaveBeenCalledWith("subtitler", expect.any(Object));
    expect(Object.keys(electron.api ?? {})).not.toContain("ipcRenderer");
  });

  it.each([
    ["getAppState", "state:get", []],
    ["getWorkflowConfig", "config:get", ["hosted"]],
    ["saveWorkflowConfig", "config:save", ["local", { alignment: { language: "ja" } }]],
    ["analyzeMedia", "media:analyze", ["C:\\media\\sample.mkv"]],
    ["getManagedLlamaStatus", "llama:status", ["vulkan", "b1234"]],
    ["startRun", "run:start", [{ workflow: "local", inputPath: "C:\\in.mkv" }]],
    ["cancelRun", "run:cancel", ["run-1"]],
  ])("maps %s to the expected IPC channel", async (method, channel, args) => {
    electron.invoke.mockResolvedValue("result");
    const call = electron.api?.[method] as (...values: unknown[]) => Promise<unknown>;

    await expect(call(...args)).resolves.toBe("result");
    expect(electron.invoke).toHaveBeenCalledWith(channel, ...args);
  });

  it("wraps run events and removes the exact listener it registered", () => {
    const callback = vi.fn();
    const unsubscribe = (electron.api?.onRunEvent as (handler: (event: unknown) => void) => () => void)(callback);
    const [channel, listener] = electron.on.mock.calls[0] as [string, (_event: unknown, payload: unknown) => void];

    expect(channel).toBe("run:event");
    listener({}, { type: "log", runId: "run-1", text: "working" });
    expect(callback).toHaveBeenCalledWith({ type: "log", runId: "run-1", text: "working" });

    unsubscribe();
    expect(electron.removeListener).toHaveBeenCalledWith("run:event", listener);
  });

  it("uses webUtils for dropped-file paths", () => {
    const file = {} as File;
    electron.getPathForFile.mockReturnValue("C:\\media\\drop.mkv");

    expect((electron.api?.filePath as (value: File) => string)(file)).toBe("C:\\media\\drop.mkv");
    expect(electron.getPathForFile).toHaveBeenCalledWith(file);
  });
});
