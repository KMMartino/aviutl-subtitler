import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { managedFfmpegBinDir, resolveFfmpegCommand } from "./ffmpegManager";
import type { RuntimePaths } from "./paths";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("ffmpeg manager", () => {
  it("uses managed ffmpeg when PATH tools are unavailable", () => {
    const paths = makePaths();
    const bin = path.join(paths.managedFfmpegRoot, "current", "ffmpeg-7", "bin");
    fs.mkdirSync(bin, { recursive: true });
    fs.writeFileSync(path.join(bin, "ffmpeg.exe"), "");
    fs.writeFileSync(path.join(bin, "ffprobe.exe"), "");

    if (resolveFfmpegCommand("ffmpeg", paths) === "ffmpeg") {
      expect(resolveFfmpegCommand("ffprobe", paths)).toBe("ffprobe");
      expect(managedFfmpegBinDir(paths)).toBe("");
    } else {
      expect(resolveFfmpegCommand("ffmpeg", paths)).toBe(path.join(bin, "ffmpeg.exe"));
      expect(resolveFfmpegCommand("ffprobe", paths)).toBe(path.join(bin, "ffprobe.exe"));
      expect(managedFfmpegBinDir(paths)).toBe(bin);
    }
  });

  it("falls back to PATH command names when managed binaries are missing", () => {
    const paths = makePaths();

    expect(resolveFfmpegCommand("ffmpeg", paths)).toBe("ffmpeg");
    expect(resolveFfmpegCommand("ffprobe", paths)).toBe("ffprobe");
    expect(managedFfmpegBinDir(paths)).toBe("");
  });
});

function makePaths(): RuntimePaths {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-ffmpeg-"));
  roots.push(root);
  return {
    appResourceRoot: root,
    bundledBackendRoot: root,
    bundledConfigRoot: path.join(root, "configs"),
    userDataRoot: root,
    stateRoot: root,
    userConfigRoot: path.join(root, "configs"),
    userToolsRoot: path.join(root, "tools"),
    userModelsRoot: path.join(root, "models"),
    managedPythonRoot: path.join(root, "python"),
    managedFfmpegRoot: path.join(root, "tools", "ffmpeg"),
    envFile: path.join(root, ".env"),
    glossaryFile: path.join(root, "glossary.txt"),
  };
}
