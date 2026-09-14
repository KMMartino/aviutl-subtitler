import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ spawn: vi.fn(), spawnSync: vi.fn() }));
vi.mock("node:child_process", () => mocks);
import { acquireSource } from "./sourceAcquisition";
import { stageSourceMedia, stageWebAsset } from "./webAssetAcquisition";

let root: string;
beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "subutl-source-test-"));
  mocks.spawn.mockReset();
  mocks.spawnSync.mockReset();
  mocks.spawn.mockImplementation((_executable: string, args: string[]) => {
    const child = Object.assign(new EventEmitter(), { stdout: new EventEmitter(), stderr: new EventEmitter(), pid: 12345, kill: vi.fn() });
    queueMicrotask(() => {
      if (args.includes("--dump-single-json")) {
        // OS pipe chunks may split a multibyte character anywhere.
        for (const byte of Buffer.from(JSON.stringify({ title: "My stream 配信", duration: 21600, webpage_url: "https://example.com/recording" }))) {
          child.stdout.emit("data", Buffer.from([byte]));
        }
      } else {
        const output = args[args.indexOf("-o") + 1].replace("%(ext)s", "mkv");
        fs.writeFileSync(output, "downloaded media");
        child.stdout.emit("data", Buffer.from("SUBUTL_PRO"));
        child.stdout.emit("data", Buffer.from("GRESS  12.5%\nother downloader output\nSUBUTL_PROGRESS 100"));
        child.stdout.emit("data", Buffer.from(".0%\n"));
        child.stdout.emit("data", Buffer.from(`${output}\n`));
      }
      child.emit("close", 0);
    });
    return child;
  });
});
afterEach(() => {
  expect(path.resolve(root).startsWith(path.join(path.resolve(os.tmpdir()), "subutl-source-test-"))).toBe(true);
  fs.rmSync(root, { recursive: true, force: true });
});

describe("source acquisition", () => {
  it("downloads a complete long recording and preserves its origin without library admission", async () => {
    const result = await acquireSource({ executablePath: "yt-dlp" }, root, "https://example.com/recording");
    expect(result.sourceStartSec).toBe(0);
    expect(result.sourceEndSec).toBe(21600);
    expect(result.origin.title).toBe("My stream 配信");
    expect(fs.existsSync(result.path)).toBe(true);
    expect(JSON.parse(fs.readFileSync(path.join(path.dirname(result.path), "source.json"), "utf8"))).toEqual(result);
    expect(mocks.spawn.mock.calls[1][1]).not.toContain("--download-sections");
  });

  it("reports download progress across pipe chunks without consuming the final path", async () => {
    const progress = vi.fn();
    const result = await acquireSource({ executablePath: "yt-dlp" }, root, "https://example.com/recording", undefined, progress);
    expect(progress.mock.calls).toEqual([[12.5], [100]]);
    expect(fs.existsSync(result.path)).toBe(true);
    expect(mocks.spawn.mock.calls[1][1]).toContain("--progress-template");
    expect(mocks.spawn.mock.calls[0][1]).toEqual(expect.arrayContaining(["--encoding", "utf-8"]));
  });

  it("keeps the library's bounded acquisition policy separate", async () => {
    const probe = { sourceUrl: "https://example.com/recording", sourcePageUrl: "https://example.com/recording", title: "Stream", creator: "", licenseText: "", durationSec: 21600, thumbnailUrl: "", extractor: "" };
    const result = await stageWebAsset({ executablePath: "yt-dlp" }, root,
      { sourceUrl: probe.sourceUrl, rightsConfirmed: true, description: "Gameplay", windowStartSec: 3600 }, probe);
    expect(result.sourceStartSec).toBe(3600);
    expect(result.sourceEndSec).toBe(4800);
    expect(mocks.spawn.mock.calls[0][1]).toContain("*3600-4800");
  });

  it("rejects invalid URLs and cancellation before starting a downloader", async () => {
    await expect(stageSourceMedia({ executablePath: "yt-dlp" }, root, "file:///private")).rejects.toThrow("HTTP(S)");
    const controller = new AbortController();
    controller.abort();
    await expect(acquireSource({ executablePath: "yt-dlp" }, root, "https://example.com/video", controller.signal)).rejects.toThrow("cancelled");
    expect(mocks.spawn).not.toHaveBeenCalled();
  });

  it("cancels the downloader's process tree", async () => {
    mocks.spawn.mockImplementation(() => Object.assign(new EventEmitter(), {
      stdout: new EventEmitter(), stderr: new EventEmitter(), pid: 12345, kill: vi.fn(),
    }));
    const controller = new AbortController();
    const pending = acquireSource({ executablePath: "yt-dlp" }, root, "https://example.com/video", controller.signal);
    controller.abort();
    await expect(pending).rejects.toThrow("cancelled");
    if (process.platform === "win32") expect(mocks.spawnSync).toHaveBeenCalledWith("taskkill", ["/PID", "12345", "/T", "/F"], expect.anything());
  });
});
