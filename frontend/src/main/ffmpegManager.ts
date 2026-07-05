import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import extract from "extract-zip";
import { runtimePaths, type RuntimePaths } from "./paths";

export type FfmpegStatus = {
  source: "path" | "managed" | "missing";
  ffmpegPath: string;
  ffprobePath: string;
  version: string;
  ready: boolean;
  error: string;
};

const ffmpegZipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip";

export async function getFfmpegStatus(paths = runtimePaths()): Promise<FfmpegStatus> {
  const pathStatus = await pathFfmpegStatus();
  if (pathStatus.ready) return pathStatus;
  const managed = managedFfmpegPaths(paths);
  if (managed.ffmpegPath && managed.ffprobePath) {
    const version = await commandVersion(managed.ffmpegPath).catch((error) => String(error));
    return { source: "managed", ...managed, version: firstLine(version), ready: true, error: "" };
  }
  return {
    source: "missing",
    ffmpegPath: "",
    ffprobePath: "",
    version: "",
    ready: false,
    error: pathStatus.error || "FFmpeg and ffprobe were not found on PATH or in the managed tools directory.",
  };
}

export async function downloadManagedFfmpeg(onLog: (text: string) => void = () => undefined, paths = runtimePaths()): Promise<FfmpegStatus> {
  onLog("$ ffmpeg download\n");
  const downloadsDir = path.join(paths.managedFfmpegRoot, "downloads");
  const zipPath = path.join(downloadsDir, "ffmpeg-release-essentials.zip");
  fs.mkdirSync(downloadsDir, { recursive: true });
  if (!fs.existsSync(zipPath)) {
    const partial = `${zipPath}.part`;
    onLog(`[ffmpeg] downloading ${ffmpegZipUrl}\n`);
    try {
      const response = await fetch(ffmpegZipUrl, { redirect: "follow", signal: AbortSignal.timeout(30 * 60 * 1000) });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      await pipeline(Readable.fromWeb(response.body as never), fs.createWriteStream(partial));
      fs.renameSync(partial, zipPath);
    } catch (error) {
      fs.rmSync(partial, { force: true });
      throw new Error(`Could not download FFmpeg: ${error instanceof Error ? error.message : String(error)}`);
    }
  } else {
    onLog(`[ffmpeg] using cached download ${zipPath}\n`);
  }

  const installDir = path.join(paths.managedFfmpegRoot, "current");
  fs.rmSync(installDir, { recursive: true, force: true });
  fs.mkdirSync(installDir, { recursive: true });
  onLog(`[ffmpeg] extracting to ${installDir}\n`);
  await extract(zipPath, { dir: installDir });
  const status = await getFfmpegStatus(paths);
  if (!status.ready || status.source !== "managed") throw new Error("FFmpeg download completed, but ffmpeg.exe and ffprobe.exe were not found.");
  onLog(`[ffmpeg] ready: ${status.ffmpegPath}\n`);
  return status;
}

export function resolveFfmpegCommand(binary: "ffmpeg" | "ffprobe", paths = runtimePaths()): string {
  if (commandExists("ffmpeg") && commandExists("ffprobe")) return binary;
  const managed = managedFfmpegPaths(paths);
  return binary === "ffmpeg" ? managed.ffmpegPath || "ffmpeg" : managed.ffprobePath || "ffprobe";
}

export function managedFfmpegBinDir(paths = runtimePaths()): string {
  if (commandExists("ffmpeg") && commandExists("ffprobe")) return "";
  const managed = managedFfmpegPaths(paths);
  return managed.ffmpegPath ? path.dirname(managed.ffmpegPath) : "";
}

function managedFfmpegPaths(paths: RuntimePaths): { ffmpegPath: string; ffprobePath: string } {
  const root = path.join(paths.managedFfmpegRoot, "current");
  const ffmpegPath = findExecutable(root, "ffmpeg.exe");
  const ffprobePath = findExecutable(root, "ffprobe.exe");
  return { ffmpegPath, ffprobePath };
}

async function pathFfmpegStatus(): Promise<FfmpegStatus> {
  try {
    const [ffmpegVersion] = await Promise.all([commandVersion("ffmpeg"), commandVersion("ffprobe")]);
    return { source: "path", ffmpegPath: "ffmpeg", ffprobePath: "ffprobe", version: firstLine(ffmpegVersion), ready: true, error: "" };
  } catch (error) {
    return { source: "missing", ffmpegPath: "", ffprobePath: "", version: "", ready: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function commandVersion(command: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, ["-version"], { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString("utf8"); });
    child.on("error", (error) => reject(error));
    child.on("exit", (code) => code === 0 ? resolve(stdout) : reject(new Error(stderr.trim() || `${command} failed`)));
  });
}

function commandExists(command: string): boolean {
  const result = spawnSync(command, ["-version"], { encoding: "utf8", timeout: 5000, windowsHide: true });
  return !result.error && result.status === 0;
}

function findExecutable(root: string, name: string): string {
  if (!fs.existsSync(root)) return "";
  const entries = fs.readdirSync(root, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(root, entry.name);
    if (entry.isFile() && entry.name.toLowerCase() === name.toLowerCase()) return full;
    if (entry.isDirectory()) {
      const found = findExecutable(full, name);
      if (found) return found;
    }
  }
  return "";
}

function firstLine(value: string): string {
  return value.split(/\r?\n/).find(Boolean) ?? "";
}
