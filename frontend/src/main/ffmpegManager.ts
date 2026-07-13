import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import extract from "extract-zip";
import { runtimePaths, type RuntimePaths } from "./paths";
import { downloadVerifiedArtifact, writeArtifactMetadata } from "./artifactIntegrity";

export type FfmpegStatus = {
  source: "path" | "managed" | "missing";
  ffmpegPath: string;
  ffprobePath: string;
  version: string;
  ready: boolean;
  managedInstalled: boolean;
  error: string;
};

export const FFMPEG_ARTIFACT = {
  version: "8.0.1",
  filename: "ffmpeg-8.0.1-essentials_build.zip",
  url: "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0.1-essentials_build.zip",
  bytes: 106_259_850,
  sha256: "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673",
} as const;

export async function getFfmpegStatus(paths = runtimePaths()): Promise<FfmpegStatus> {
  const pathStatus = await pathFfmpegStatus();
  const managed = managedFfmpegPaths(paths);
  const managedInstalled = Boolean(managed.ffmpegPath && managed.ffprobePath);
  if (pathStatus.ready) return { ...pathStatus, managedInstalled };
  if (managed.ffmpegPath && managed.ffprobePath) {
    const version = await commandVersion(managed.ffmpegPath).catch((error) => String(error));
    return { source: "managed", ...managed, version: firstLine(version), ready: true, managedInstalled, error: "" };
  }
  return {
    source: "missing",
    ffmpegPath: "",
    ffprobePath: "",
    version: "",
    ready: false,
    managedInstalled,
    error: pathStatus.error || "FFmpeg and ffprobe were not found on PATH or in the managed tools directory.",
  };
}

export async function downloadManagedFfmpeg(onLog: (text: string) => void = () => undefined, paths = runtimePaths()): Promise<FfmpegStatus> {
  onLog("$ ffmpeg download\n");
  const downloadsDir = path.join(paths.managedFfmpegRoot, "downloads");
  const zipPath = path.join(downloadsDir, FFMPEG_ARTIFACT.filename);
  fs.mkdirSync(downloadsDir, { recursive: true });
  onLog(`[ffmpeg] acquiring pinned ${FFMPEG_ARTIFACT.version} artifact\n`);
  try {
    let lastPercent = -1;
    await downloadVerifiedArtifact(FFMPEG_ARTIFACT.url, zipPath, FFMPEG_ARTIFACT, (received, total) => {
      const percent = Math.floor(received / total * 100);
      if (percent !== lastPercent && percent % 5 === 0) {
        lastPercent = percent;
        onLog(`[ffmpeg] download ${percent}%\n`);
      }
    });
  } catch (error) {
    throw new Error(`Could not download verified FFmpeg: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
  }
  onLog(`[ffmpeg] verified SHA-256 ${FFMPEG_ARTIFACT.sha256}\n`);

  const installDir = path.join(paths.managedFfmpegRoot, "current");
  const stagingDir = `${installDir}.part`;
  fs.rmSync(stagingDir, { recursive: true, force: true });
  fs.mkdirSync(stagingDir, { recursive: true });
  onLog(`[ffmpeg] extracting to staging directory\n`);
  await extract(zipPath, { dir: stagingDir });
  fs.rmSync(installDir, { recursive: true, force: true });
  fs.renameSync(stagingDir, installDir);
  writeArtifactMetadata(path.join(installDir, "artifact.json"), {
    source: FFMPEG_ARTIFACT.url,
    bytes: FFMPEG_ARTIFACT.bytes,
    sha256: FFMPEG_ARTIFACT.sha256,
    revision: FFMPEG_ARTIFACT.version,
    installedAt: new Date().toISOString(),
  });
  const status = await getFfmpegStatus(paths);
  if (!status.ready || status.source !== "managed") throw new Error("FFmpeg download completed, but ffmpeg.exe and ffprobe.exe were not found.");
  onLog(`[ffmpeg] ready: ${status.ffmpegPath}\n`);
  return status;
}

export async function deleteManagedFfmpeg(paths = runtimePaths()): Promise<FfmpegStatus> {
  if (!isWithin(paths.userToolsRoot, paths.managedFfmpegRoot)) throw new Error("Refusing to delete FFmpeg outside the managed app tools directory.");
  fs.rmSync(paths.managedFfmpegRoot, { recursive: true, force: true });
  return getFfmpegStatus(paths);
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
  if (!hasExpectedMetadata(path.join(root, "artifact.json"))) return { ffmpegPath: "", ffprobePath: "" };
  const ffmpegPath = findExecutable(root, "ffmpeg.exe");
  const ffprobePath = findExecutable(root, "ffprobe.exe");
  return { ffmpegPath, ffprobePath };
}

function hasExpectedMetadata(file: string): boolean {
  try {
    const value = JSON.parse(fs.readFileSync(file, "utf8")) as { bytes?: number; sha256?: string; revision?: string };
    return value.bytes === FFMPEG_ARTIFACT.bytes && value.sha256 === FFMPEG_ARTIFACT.sha256 && value.revision === FFMPEG_ARTIFACT.version;
  } catch {
    return false;
  }
}

async function pathFfmpegStatus(): Promise<FfmpegStatus> {
  try {
    const [ffmpegVersion] = await Promise.all([commandVersion("ffmpeg"), commandVersion("ffprobe")]);
    return { source: "path", ffmpegPath: "ffmpeg", ffprobePath: "ffprobe", version: firstLine(ffmpegVersion), ready: true, managedInstalled: false, error: "" };
  } catch (error) {
    return { source: "missing", ffmpegPath: "", ffprobePath: "", version: "", ready: false, managedInstalled: false, error: error instanceof Error ? error.message : String(error) };
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

function isWithin(root: string, target: string): boolean {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}
