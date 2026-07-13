import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const children: FakeChild[] = [];
vi.mock("node:child_process", () => ({
  spawn: vi.fn(() => {
    const child = new FakeChild();
    children.push(child);
    return child;
  }),
}));
vi.mock("./ffmpegManager", () => ({ resolveFfmpegCommand: (name: string) => name }));

import { MediaAnalysisCoordinator, runBounded } from "./mediaAnalyzer";

class FakeChild extends EventEmitter {
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  killed = false;
  kill(): boolean { this.killed = true; return true; }
}

beforeEach(() => { children.splice(0); });
afterEach(() => { vi.useRealTimers(); });

describe("bounded media process", () => {
  it("terminates when superseded", async () => {
    const controller = new AbortController();
    const result = runBounded("ffprobe", [], 100, controller.signal);
    controller.abort();
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(children[0].killed).toBe(true);
  });

  it("terminates when output exceeds its bound", async () => {
    const result = runBounded("ffprobe", [], 3);
    children[0].stdout.emit("data", Buffer.from("four"));
    await expect(result).rejects.toThrow(/output exceeded/);
    expect(children[0].killed).toBe(true);
  });

  it("returns bounded successful output", async () => {
    const result = runBounded("ffprobe", [], 100);
    children[0].stdout.emit("data", Buffer.from("{}"));
    children[0].emit("close", 0);
    await expect(result).resolves.toBe("{}");
  });

  it("terminates a probe that exceeds its deadline", async () => {
    vi.useFakeTimers();
    const result = runBounded("ffprobe", [], 100);
    const assertion = expect(result).rejects.toThrow(/timed out/);
    await vi.runAllTimersAsync();
    await assertion;
    expect(children[0].killed).toBe(true);
  });

  it("aborts a stale probe when a newer analysis starts", async () => {
    const coordinator = new MediaAnalysisCoordinator();
    const first = coordinator.analyze("C:\\first.mp4");
    const firstAssertion = expect(first).rejects.toMatchObject({ name: "AbortError" });
    const second = coordinator.analyze("C:\\second.mp4");
    const secondAssertion = expect(second).rejects.toMatchObject({ name: "AbortError" });
    expect(children[0].killed).toBe(true);
    coordinator.cancel();
    await firstAssertion;
    await secondAssertion;
    expect(children[1].killed).toBe(true);
  });

  it("starts a fresh analysis when the same path is selected again", async () => {
    const coordinator = new MediaAnalysisCoordinator();
    const first = coordinator.analyze("C:\\same.mp4");
    const firstAssertion = expect(first).rejects.toMatchObject({ name: "AbortError" });
    const second = coordinator.analyze("C:\\same.mp4");

    expect(children[0].killed).toBe(true);
    children[1].stdout.emit("data", Buffer.from("{}"));
    children[1].emit("close", 0);

    await firstAssertion;
    await expect(second).resolves.toMatchObject({ audioTracks: [], thumbnailDataUrl: "" });
  });
});
