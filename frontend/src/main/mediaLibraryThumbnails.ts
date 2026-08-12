import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { MediaAssetSummary } from "../renderer/lib/types";

const THUMBNAIL_WIDTH = 192;
const THUMBNAIL_HEIGHT = 108;
const TIMEOUT_MS = 60_000;

export async function ensureMediaThumbnail(
  asset: MediaAssetSummary,
  thumbnailRoot: string,
  ffmpeg: string,
): Promise<string> {
  if (asset.availability !== "active" || !fs.existsSync(asset.canonicalPath)) return "";
  fs.mkdirSync(thumbnailRoot, { recursive: true });
  const output = path.join(thumbnailRoot, `${asset.quickFingerprint}-${THUMBNAIL_WIDTH}x${THUMBNAIL_HEIGHT}.jpg`);
  if (fs.existsSync(output) && fs.statSync(output).size > 0) return output;
  const temporary = `${output}.${crypto.randomUUID()}.tmp.jpg`;
  const args = ["-hide_banner", "-loglevel", "error", "-y"];
  if (asset.mediaKind === "video") {
    const durationSec = (asset.durationMs ?? 0) / 1000;
    args.push("-ss", String(Math.min(30, Math.max(0, durationSec * .1))));
  }
  args.push(
    "-i", asset.canonicalPath,
    "-frames:v", "1",
    "-vf", `scale=${THUMBNAIL_WIDTH}:${THUMBNAIL_HEIGHT}:force_original_aspect_ratio=decrease,pad=${THUMBNAIL_WIDTH}:${THUMBNAIL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x111111`,
    "-q:v", "5",
    temporary,
  );
  try {
    await runFfmpeg(ffmpeg, args);
    if (fs.existsSync(output)) fs.rmSync(temporary, { force: true });
    else fs.renameSync(temporary, output);
    return output;
  } catch {
    fs.rmSync(temporary, { force: true });
    return fs.existsSync(output) ? output : "";
  }
}

function runFfmpeg(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stderr = "";
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve();
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(new Error("Thumbnail generation timed out."));
    }, TIMEOUT_MS);
    timer.unref();
    child.stderr.on("data", (chunk: Buffer) => {
      if (stderr.length < 100_000) stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => finish(error));
    child.on("close", (code) => {
      if (code === 0) finish();
      else finish(new Error(stderr.trim() || `Thumbnail FFmpeg failed with code ${code}.`));
    });
  });
}
