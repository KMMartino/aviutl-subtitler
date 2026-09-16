import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { terminateProcessTree } from "./processTree";
import type { WebAssetAcquireRequest, WebAssetProbe } from "../renderer/lib/types";

const MAX_CAPTURE_BYTES = 10 * 1024 * 1024;
const WHOLE_SOURCE_LIMIT_SEC = 20 * 60;
const LONG_SOURCE_WINDOW_SEC = 20 * 60;

export type YtDlpInvocation = {
  onOutdated?: () => void;
  executablePath: string;
  denoPath?: string;
  cookiesBrowser?: string;
  cookiesProfile?: string;
  ffmpegLocation?: string;
};

export async function probeWebAsset(invocation: YtDlpInvocation, sourceUrl: string, signal?: AbortSignal): Promise<WebAssetProbe> {
  const url = validatedWebUrl(sourceUrl);
  const result = await runYtDlp(
    invocation,
    ["--dump-single-json", "--no-playlist", "--skip-download", url],
    120_000, signal,
  );
  let metadata: Record<string, unknown>;
  try {
    metadata = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch {
    throw new Error("yt-dlp returned invalid source metadata.");
  }
  const duration = finiteNumber(metadata.duration);
  if (metadata.is_live === true || metadata.live_status === "is_live" || metadata.live_status === "is_upcoming") {
    throw new Error("Use a completed video or archived livestream (VOD).");
  }
  return {
    sourceUrl: url,
    sourcePageUrl: stringValue(metadata.webpage_url) || url,
    title: stringValue(metadata.title) || "Web media",
    creator: stringValue(metadata.uploader) || stringValue(metadata.channel),
    licenseText: stringValue(metadata.license),
    durationSec: duration !== null && duration >= 0 ? duration : null,
    thumbnailUrl: validatedOptionalWebUrl(metadata.thumbnail),
    extractor: stringValue(metadata.extractor),
  };
}

export async function stageWebAsset(
  invocation: YtDlpInvocation,
  stagingRoot: string,
  request: WebAssetAcquireRequest,
  probe: WebAssetProbe,
): Promise<{ stagedPath: string; sourceStartSec: number; sourceEndSec: number | null }> {
  if (!request.rightsConfirmed) throw new Error("Confirm that you have the right to use this media before downloading it.");
  if (!request.description.trim()) throw new Error("A final description is required before a web asset can enter the library.");
  let sourceStartSec = 0;
  let sourceEndSec: number | null = null;
  if (probe.durationSec !== null && probe.durationSec > WHOLE_SOURCE_LIMIT_SEC) {
    sourceStartSec = clamp(request.windowStartSec ?? 0, 0, Math.max(0, probe.durationSec - 1));
    sourceEndSec = Math.min(probe.durationSec, sourceStartSec + LONG_SOURCE_WINDOW_SEC);
  }
  return stageSourceMedia(invocation, stagingRoot, request.sourceUrl, { sourceStartSec, sourceEndSec });
}

export async function stageSourceMedia(
  invocation: YtDlpInvocation, stagingRoot: string, url: string,
  range: { sourceStartSec: number; sourceEndSec: number | null } = { sourceStartSec: 0, sourceEndSec: null },
  signal?: AbortSignal,
  onProgress?: (percent: number) => void,
): Promise<{ stagedPath: string; sourceStartSec: number; sourceEndSec: number | null }> {
  const { sourceStartSec, sourceEndSec } = range;
  if (!Number.isFinite(sourceStartSec) || sourceStartSec < 0 || (sourceEndSec === null && sourceStartSec !== 0) || (sourceEndSec !== null && (!Number.isFinite(sourceEndSec) || sourceEndSec <= sourceStartSec))) {
    throw new Error("Invalid source download range.");
  }
  const sourceUrl = validatedWebUrl(url);
  const jobId = crypto.randomUUID();
  const jobRoot = path.join(stagingRoot, jobId);
  fs.mkdirSync(jobRoot, { recursive: true });
  const outputTemplate = path.join(jobRoot, "download.%(ext)s");
  const args = [
    "--no-playlist",
    "--restrict-filenames",
    // Bound individual HTTP requests when downloading multi-GB recordings.
    "--http-chunk-size", "10M",
    "--socket-timeout", "30",
    "--merge-output-format", "mkv",
    "--print", "after_move:filepath",
    "-f", "bv*+ba/b",
    "-o", outputTemplate,
  ];
  if (onProgress) args.push("--progress", "--newline", "--progress-delta", "1", "--progress-template", "download:SUBUTL_PROGRESS %(progress._percent_str)s");
  if (sourceEndSec !== null) args.push("--download-sections", `*${sourceStartSec}-${sourceEndSec}`, "--force-keyframes-at-cuts");
  args.push(sourceUrl);
  try {
    const result = await runYtDlp(invocation, args, 60 * 60_000, signal, onProgress);
    const reported = result.stdout.trim().split(/\r?\n/).filter(Boolean).at(-1);
    if (!reported) throw new Error("yt-dlp completed without reporting a downloaded file.");
    const stagedPath = path.resolve(reported);
    const resolvedJobRoot = path.resolve(jobRoot);
    if (!stagedPath.startsWith(`${resolvedJobRoot}${path.sep}`) || !fs.statSync(stagedPath).isFile()) {
      throw new Error("yt-dlp reported an invalid staged file.");
    }
    return { stagedPath, sourceStartSec, sourceEndSec };
  } catch (error) {
    const relative = path.relative(path.resolve(stagingRoot), path.resolve(jobRoot));
    if (relative && !relative.startsWith("..") && !path.isAbsolute(relative)) {
      // Only this operation's UUID directory is eligible for partial-file cleanup.
      try { fs.rmSync(jobRoot, { recursive: true, force: true }); } catch { /* A stopped downloader may still be releasing its file. */ }
    }
    throw error;
  }
}

export function promoteStagedAsset(stagedPath: string, managedAssetsRoot: string): string {
  const extension = path.extname(stagedPath).toLowerCase() || ".mkv";
  fs.mkdirSync(managedAssetsRoot, { recursive: true });
  const destination = path.join(managedAssetsRoot, `${crypto.randomUUID()}${extension}`);
  fs.renameSync(stagedPath, destination);
  return destination;
}

async function runYtDlp(
  invocation: YtDlpInvocation,
  args: string[],
  timeoutMs: number,
  signal?: AbortSignal,
  onProgress?: (percent: number) => void,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error("Source download cancelled.")); return; }
    const commonArgs = ["--ignore-config", "--encoding", "utf-8"];
    if (invocation.denoPath) commonArgs.push("--js-runtimes", `deno:${invocation.denoPath}`);
    if (invocation.cookiesBrowser) {
      const cookieSource = invocation.cookiesProfile
        ? `${invocation.cookiesBrowser}:${invocation.cookiesProfile}`
        : invocation.cookiesBrowser;
      commonArgs.push("--cookies-from-browser", cookieSource);
    }
    if (invocation.ffmpegLocation) commonArgs.push("--ffmpeg-location", invocation.ffmpegLocation);
    const child = spawn(invocation.executablePath, [...commonArgs, ...args], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let outdatedNotified = false;
    const checkOutdated = () => {
      if (!outdatedNotified && isYtDlpOutdatedWarning(stderr)) { outdatedNotified = true; invocation.onOutdated?.(); }
    };
    const stdoutDecoder = new StringDecoder("utf8");
    const stderrDecoder = new StringDecoder("utf8");
    let progressBuffer = "";
    let captureBytes = 0;
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      action();
    };
    const abort = () => {
      terminateProcessTree(child, true);
      finish(() => reject(new Error("Source download cancelled.")));
    };
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => {
      terminateProcessTree(child, true);
      finish(() => reject(new Error("The web-media operation timed out.")));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      captureBytes += chunk.length;
      if (captureBytes > MAX_CAPTURE_BYTES) terminateProcessTree(child, true);
      else {
        const text = stdoutDecoder.write(chunk);
        stdout += text;
        if (onProgress) {
          const lines = (progressBuffer + text).split(/\r?\n/);
          progressBuffer = lines.pop() ?? "";
          for (const line of lines) {
            const match = /^SUBUTL_PROGRESS\s+(\d+(?:\.\d+)?)%\s*$/.exec(line);
            if (match) onProgress(clamp(Number(match[1]), 0, 100));
          }
        }
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      captureBytes += chunk.length;
      if (captureBytes > MAX_CAPTURE_BYTES) terminateProcessTree(child, true);
      else { stderr += stderrDecoder.write(chunk); checkOutdated(); }
    });
    child.on("error", (error) => finish(() => reject(new Error(`Could not start yt-dlp: ${error.message}`))));
    child.on("close", (code) => finish(() => {
      stdout += stdoutDecoder.end();
      stderr += stderrDecoder.end();
      checkOutdated();
      if (captureBytes > MAX_CAPTURE_BYTES) reject(new Error("yt-dlp produced too much output."));
      else if (code !== 0) reject(new Error(`yt-dlp failed: ${stderr.trim() || `exit code ${code}`}`));
      else resolve({ stdout, stderr });
    }));
  });
}

export function isYtDlpOutdatedWarning(text: string): boolean {
  return /(?:yt-dlp.{0,120}(?:older than|outdated|out.of.date)|(?:older than|outdated|out.of.date).{0,120}yt-dlp)/isu.test(text);
}

function validatedWebUrl(value: string): string {
  let url: URL;
  try { url = new URL(value); } catch { throw new Error("Enter a valid web URL."); }
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("Only public HTTP(S) media URLs are supported.");
  return url.href;
}

function validatedOptionalWebUrl(value: unknown): string {
  const text = stringValue(value);
  if (!text) return "";
  try { return validatedWebUrl(text); } catch { return ""; }
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? value : minimum));
}
