import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ spawn: vi.fn(), spawnSync: vi.fn() }));
vi.mock("node:child_process", () => mocks);
import { acquireSource } from "./sourceAcquisition";
import { isYtDlpOutdatedWarning, stageSourceMedia, stageWebAsset } from "./webAssetAcquisition";

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
  it("stores downloaded media and its manifest inside the chosen project or external folder", async () => {
    for (const directory of [path.join(root, "project", "Sources"), path.join(root, "persistent-downloads")]) {
      const result = await acquireSource({ executablePath: "yt-dlp" }, directory, "https://example.com/recording");
      const relative = path.relative(directory, result.path);
      expect(relative.startsWith("..")).toBe(false);
      expect(path.isAbsolute(relative)).toBe(false);
      expect(fs.readFileSync(result.path, "utf8")).toBe("downloaded media");
      expect(JSON.parse(fs.readFileSync(path.join(path.dirname(result.path), "source.json"), "utf8")).path).toBe(result.path);
    }
  });
  it("downloads a requested VOD range and preserves its original time offset", async () => {
    const result = await acquireSource({ executablePath: "yt-dlp" }, root, "https://example.com/recording", undefined, undefined,
      { startSec: 3600, endSec: 3900 });
    expect(result.sourceStartSec).toBe(3600);
    expect(result.sourceEndSec).toBe(3900);
    expect(mocks.spawn.mock.calls[1][1]).toEqual(expect.arrayContaining(["--download-sections", "*3600-3900", "--force-keyframes-at-cuts"]));
    expect(mocks.spawn.mock.calls[0][1]).not.toContain("--no-warnings");
  });

  it("rejects ranges outside the VOD before downloading", async () => {
    await expect(acquireSource({ executablePath: "yt-dlp" }, root, "https://example.com/recording", undefined, undefined,
      { startSec: 100, endSec: 22000 })).rejects.toThrow("within the recording");
    expect(mocks.spawn).toHaveBeenCalledTimes(1);
  });

  it("recognizes age warnings without treating unrelated errors as an update request", () => {
    expect(isYtDlpOutdatedWarning("WARNING: Your yt-dlp version (2025.01.01) is older than 90 days! Please update")).toBe(true);
    expect(isYtDlpOutdatedWarning("WARNING: yt-dlp is out-of-date")).toBe(true);
    expect(isYtDlpOutdatedWarning("ERROR: Sign in to confirm your age")).toBe(false);
  });

  it("reports outdated warnings split across chunks once per process", async () => {
    const original = mocks.spawn.getMockImplementation()!;
    mocks.spawn.mockImplementation((...args) => {
      const child = original(...args);
      queueMicrotask(() => {
        child.stderr.emit("data", Buffer.from("WARNING: Your yt-dlp version is older "));
        child.stderr.emit("data", Buffer.from("than 90 days. Update yt-dlp.\n"));
      });
      return child;
    });
    const outdated = vi.fn();
    await acquireSource({ executablePath: "yt-dlp", onOutdated: outdated }, root, "https://example.com/recording");
    expect(outdated).toHaveBeenCalled();
  });
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
