import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import type { WebAssetAcquireRequest, WebAssetProbe } from "../renderer/lib/types";

const MAX_CAPTURE_BYTES = 10 * 1024 * 1024;
const WHOLE_SOURCE_LIMIT_SEC = 20 * 60;
const LONG_SOURCE_WINDOW_SEC = 20 * 60;

export type YtDlpInvocation = {
  executablePath: string;
  denoPath?: string;
  cookiesBrowser?: string;
  cookiesProfile?: string;
  ffmpegLocation?: string;
};

export async function probeWebAsset(invocation: YtDlpInvocation, sourceUrl: string): Promise<WebAssetProbe> {
  const url = validatedWebUrl(sourceUrl);
  const result = await runYtDlp(
    invocation,
    ["--dump-single-json", "--no-playlist", "--skip-download", "--no-warnings", url],
    120_000,
  );
  let metadata: Record<string, unknown>;
  try {
    metadata = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch {
    throw new Error("yt-dlp returned invalid source metadata.");
  }
  const duration = finiteNumber(metadata.duration);
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
  const sourceUrl = validatedWebUrl(request.sourceUrl);
  const jobId = crypto.randomUUID();
  const jobRoot = path.join(stagingRoot, jobId);
  fs.mkdirSync(jobRoot, { recursive: true });
  const outputTemplate = path.join(jobRoot, "download.%(ext)s");
  const args = [
    "--no-playlist",
    "--no-warnings",
    "--restrict-filenames",
    "--merge-output-format", "mkv",
    "--print", "after_move:filepath",
    "-f", "bv*+ba/b",
    "-o", outputTemplate,
  ];
  let sourceStartSec = 0;
  let sourceEndSec: number | null = null;
  if (probe.durationSec !== null && probe.durationSec > WHOLE_SOURCE_LIMIT_SEC) {
    sourceStartSec = clamp(request.windowStartSec ?? 0, 0, Math.max(0, probe.durationSec - 1));
    sourceEndSec = Math.min(probe.durationSec, sourceStartSec + LONG_SOURCE_WINDOW_SEC);
    args.push("--download-sections", `*${sourceStartSec}-${sourceEndSec}`);
  }
  args.push(sourceUrl);
  const result = await runYtDlp(invocation, args, 30 * 60_000);
  const reported = result.stdout.trim().split(/\r?\n/).filter(Boolean).at(-1);
  if (!reported) throw new Error("yt-dlp completed without reporting a downloaded file.");
  const stagedPath = path.resolve(reported);
  const resolvedJobRoot = path.resolve(jobRoot);
  if (!stagedPath.startsWith(`${resolvedJobRoot}${path.sep}`) || !fs.statSync(stagedPath).isFile()) {
    throw new Error("yt-dlp reported an invalid staged file.");
  }
  return { stagedPath, sourceStartSec, sourceEndSec };
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
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const commonArgs = ["--ignore-config"];
    if (invocation.denoPath) commonArgs.push("--js-runtimes", `deno:${invocation.denoPath}`);
    if (invocation.cookiesBrowser) {
      const cookieSource = invocation.cookiesProfile
        ? `${invocation.cookiesBrowser}:${invocation.cookiesProfile}`
        : invocation.cookiesBrowser;
      commonArgs.push("--cookies-from-browser", cookieSource);
    }
    if (invocation.ffmpegLocation) commonArgs.push("--ffmpeg-location", invocation.ffmpegLocation);
    const child = spawn(invocation.executablePath, [...commonArgs, ...args], { windowsHide: true });
    let stdout = "";
    let stderr = "";
    let captureBytes = 0;
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      action();
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(() => reject(new Error("The web-media operation timed out.")));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      captureBytes += chunk.length;
      if (captureBytes > MAX_CAPTURE_BYTES) child.kill();
      else stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      captureBytes += chunk.length;
      if (captureBytes > MAX_CAPTURE_BYTES) child.kill();
      else stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => finish(() => reject(new Error(`Could not start yt-dlp: ${error.message}`))));
    child.on("close", (code) => finish(() => {
      if (captureBytes > MAX_CAPTURE_BYTES) reject(new Error("yt-dlp produced too much output."));
      else if (code !== 0) reject(new Error(`yt-dlp failed: ${stderr.trim() || `exit code ${code}`}`));
      else resolve({ stdout, stderr });
    }));
  });
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
