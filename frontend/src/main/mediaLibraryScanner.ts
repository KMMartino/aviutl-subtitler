import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import type { MediaAssetKind, MediaTransparency } from "../renderer/lib/types";
import type { IndexedMediaFile } from "./mediaLibraryDatabase";

const VIDEO_EXTENSIONS = new Set([
  ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".webm", ".wmv",
]);
const IMAGE_EXTENSIONS = new Set([
  ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
]);
const QUICK_HASH_BYTES = 64 * 1024;
const PROBE_LIMIT = 2 * 1024 * 1024;
const PROBE_TIMEOUT_MS = 30_000;

type ProbeStream = {
  codec_type?: string;
  codec_name?: string;
  width?: number;
  height?: number;
  avg_frame_rate?: string;
  r_frame_rate?: string;
  pix_fmt?: string;
};

type ProbeOutput = {
  format?: { duration?: string | number };
  streams?: ProbeStream[];
};

export type MediaDirectoryEntry = {
  relativePath: string;
  name: string;
  depth: number;
  directFileCount: number;
};

export async function enumerateMediaDirectories(
  rootPath: string,
  maximumDirectories = 5_000,
): Promise<MediaDirectoryEntry[]> {
  const root = path.resolve(rootPath);
  const result: MediaDirectoryEntry[] = [];
  const pending = [root];
  while (pending.length) {
    const current = pending.pop()!;
    const entries = await fs.promises.readdir(current, { withFileTypes: true });
    const relativePath = path.relative(root, current);
    result.push({
      relativePath,
      name: relativePath ? path.basename(current) : path.basename(root),
      depth: relativePath ? relativePath.split(path.sep).length : 0,
      directFileCount: entries.filter((entry) => entry.isFile() && mediaKind(entry.name)).length,
    });
    if (result.length > maximumDirectories) {
      throw new Error(`This location contains more than ${maximumDirectories.toLocaleString()} directories.`);
    }
    const children = entries
      .filter((entry) => entry.isDirectory() && !entry.isSymbolicLink())
      .map((entry) => path.join(current, entry.name))
      .sort((left, right) => right.localeCompare(left));
    pending.push(...children);
  }
  return result.sort((left, right) => left.relativePath.localeCompare(right.relativePath));
}

export async function enumerateMediaFiles(
  rootPath: string,
  recursive: boolean,
  maximum = Number.MAX_SAFE_INTEGER,
): Promise<Array<{ path: string; kind: MediaAssetKind }>> {
  const result: Array<{ path: string; kind: MediaAssetKind }> = [];
  const pending = [rootPath];
  while (pending.length) {
    const current = pending.pop()!;
    const entries = await fs.promises.readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const target = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (recursive) pending.push(target);
        continue;
      }
      if (!entry.isFile()) continue;
      const kind = mediaKind(target);
      if (kind) result.push({ path: target, kind });
      if (result.length >= maximum) return result;
    }
  }
  return result.sort((left, right) => left.path.localeCompare(right.path));
}

export async function inspectMediaFile(
  filePath: string,
  rootPath: string,
  kind: MediaAssetKind,
  ffprobePath: string,
): Promise<IndexedMediaFile> {
  const stat = await fs.promises.stat(filePath, { bigint: true });
  const base = {
    canonicalPath: path.resolve(filePath),
    relativePath: path.relative(rootPath, filePath),
    mediaKind: kind,
    sizeBytes: Number(stat.size),
    mtimeNs: stat.mtimeNs.toString(),
    quickFingerprint: await quickFingerprint(filePath, stat.size),
  };
  try {
    const probe = await probeMedia(ffprobePath, filePath);
    const video = probe.streams?.find((stream) => stream.codec_type === "video");
    const audio = probe.streams?.find((stream) => stream.codec_type === "audio");
    const [frameRateNum, frameRateDen] = parseRate(video?.avg_frame_rate || video?.r_frame_rate);
    return {
      ...base,
      durationMs: finiteMilliseconds(probe.format?.duration),
      width: finitePositiveInteger(video?.width),
      height: finitePositiveInteger(video?.height),
      frameRateNum,
      frameRateDen,
      videoCodec: String(video?.codec_name ?? ""),
      audioCodec: String(audio?.codec_name ?? ""),
      hasAudio: Boolean(audio),
      transparency: imageTransparency(filePath, kind, video?.pix_fmt),
      availability: video ? "active" : "incompatible",
      error: video ? "" : "No readable video or image stream was found.",
    };
  } catch (error) {
    return {
      ...base,
      durationMs: null,
      width: null,
      height: null,
      frameRateNum: null,
      frameRateDen: null,
      videoCodec: "",
      audioCodec: "",
      hasAudio: false,
      transparency: kind === "image" ? "unknown" : "unsupported",
      availability: "incompatible",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function mediaKind(filePath: string): MediaAssetKind | null {
  const extension = path.extname(filePath).toLowerCase();
  if (VIDEO_EXTENSIONS.has(extension)) return "video";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  return null;
}

async function quickFingerprint(filePath: string, size: bigint): Promise<string> {
  const handle = await fs.promises.open(filePath, "r");
  try {
    const hash = crypto.createHash("sha256");
    hash.update(size.toString());
    const firstLength = Number(size < BigInt(QUICK_HASH_BYTES) ? size : BigInt(QUICK_HASH_BYTES));
    if (firstLength > 0) {
      const first = Buffer.alloc(firstLength);
      await handle.read(first, 0, firstLength, 0);
      hash.update(first);
    }
    if (size > BigInt(QUICK_HASH_BYTES)) {
      const lastLength = Number(size < BigInt(QUICK_HASH_BYTES) ? size : BigInt(QUICK_HASH_BYTES));
      const last = Buffer.alloc(lastLength);
      await handle.read(last, 0, lastLength, size - BigInt(lastLength));
      hash.update(last);
    }
    return hash.digest("hex");
  } finally {
    await handle.close();
  }
}

function probeMedia(command: string, filePath: string): Promise<ProbeOutput> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, [
      "-v", "error",
      "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,pix_fmt",
      "-of", "json",
      filePath,
    ], { windowsHide: true });
    const stdout: Buffer[] = [];
    let bytes = 0;
    let stderr = "";
    let settled = false;
    const finish = (error?: Error, value?: ProbeOutput) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(value ?? {});
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(new Error("FFprobe timed out."));
    }, PROBE_TIMEOUT_MS);
    timer.unref();
    child.stdout.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > PROBE_LIMIT) {
        child.kill();
        finish(new Error("FFprobe output exceeded the safety limit."));
        return;
      }
      stdout.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      if (stderr.length < PROBE_LIMIT) stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => finish(new Error(`Could not start FFprobe: ${error.message}`)));
    child.on("close", (code) => {
      if (settled) return;
      if (code !== 0) return finish(new Error(stderr.trim() || `FFprobe failed with code ${code}.`));
      try {
        finish(undefined, JSON.parse(Buffer.concat(stdout).toString("utf8")) as ProbeOutput);
      } catch {
        finish(new Error("FFprobe returned malformed JSON."));
      }
    });
  });
}

export function imageTransparency(
  filePath: string,
  kind: MediaAssetKind,
  pixelFormat: string | undefined,
): MediaTransparency {
  if (kind !== "image") return "unsupported";
  const extension = path.extname(filePath).toLowerCase();
  if (extension === ".jpg" || extension === ".jpeg") return "unsupported";
  const normalized = (pixelFormat ?? "").toLowerCase();
  if (!normalized) return "unknown";
  if (/^(?:rgba|bgra|argb|abgr|ya\d*|yuva|gbrap)/u.test(normalized)) return "present";
  if (normalized === "pal8") return "unknown";
  return "absent";
}

function parseRate(value: string | undefined): [number | null, number | null] {
  const match = /^(\d+)\/(\d+)$/.exec(value ?? "");
  if (!match) return [null, null];
  const numerator = Number(match[1]);
  const denominator = Number(match[2]);
  return numerator > 0 && denominator > 0 ? [numerator, denominator] : [null, null];
}

function finiteMilliseconds(value: string | number | undefined): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? Math.round(number * 1000) : null;
}

function finitePositiveInteger(value: number | undefined): number | null {
  return Number.isSafeInteger(value) && Number(value) > 0 ? Number(value) : null;
}
