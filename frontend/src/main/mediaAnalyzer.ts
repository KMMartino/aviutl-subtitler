import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import type { AudioTrackInfo, MediaAnalysis } from "../renderer/lib/types";
import { resolveFfmpegCommand } from "./ffmpegManager";

type ProbeData = {
  format?: { duration?: string; format_name?: string };
  streams?: Array<{ index?: number; codec_type?: string; codec_name?: string; width?: number; height?: number; sample_rate?: string; channels?: number; channel_layout?: string; tags?: { language?: string; title?: string } }>;
};

export const MEDIA_ANALYSIS_TIMEOUT_MS = 30_000;
export const PROBE_OUTPUT_LIMIT = 2 * 1024 * 1024;
export const THUMBNAIL_OUTPUT_LIMIT = 8 * 1024 * 1024;

export async function analyzeMedia(inputPath: string, signal?: AbortSignal): Promise<MediaAnalysis> {
  const data = JSON.parse(await runBounded(resolveFfmpegCommand("ffprobe"), [
    "-v", "error", "-show_entries", "format=duration,format_name:stream=index,codec_type,codec_name,width,height,sample_rate,channels,channel_layout:stream_tags=language,title",
    "-of", "json", inputPath,
  ], PROBE_OUTPUT_LIMIT, signal)) as ProbeData;
  const video = (data.streams ?? []).find((stream) => stream.codec_type === "video");
  const audioTracks: AudioTrackInfo[] = (data.streams ?? []).filter((stream) => stream.codec_type === "audio").map((stream, audioIndex) => ({
    audioIndex, streamIndex: Number(stream.index ?? audioIndex), codec: String(stream.codec_name ?? "unknown"),
    sampleRate: stream.sample_rate ? Number(stream.sample_rate) : null, channels: stream.channels ?? null,
    channelLayout: String(stream.channel_layout ?? ""), language: String(stream.tags?.language ?? ""), title: String(stream.tags?.title ?? ""),
  }));
  const thumbnail = video ? await runBounded(resolveFfmpegCommand("ffmpeg"), [
    "-v", "error", "-ss", "0", "-i", inputPath, "-map", "0:v:0", "-frames:v", "1",
    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
  ], THUMBNAIL_OUTPUT_LIMIT, signal, true).catch((error) => {
    if (isAbort(error)) throw error;
    return "";
  }) : "";
  return {
    durationSeconds: data.format?.duration ? Number(data.format.duration) : null,
    formatName: String(data.format?.format_name ?? ""), videoCodec: String(video?.codec_name ?? ""),
    width: video?.width ?? null, height: video?.height ?? null,
    thumbnailDataUrl: thumbnail ? `data:image/jpeg;base64,${Buffer.from(thumbnail, "latin1").toString("base64")}` : "", audioTracks,
  };
}

export function runBounded(command: string, args: string[], outputLimit: number, signal?: AbortSignal, binary = false): Promise<string> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const child: ChildProcessWithoutNullStreams = spawn(command, args, { windowsHide: true });
    const stdout: Buffer[] = [];
    let stdoutBytes = 0;
    let stderr = "";
    let settled = false;
    const finish = (error?: Error, value = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (error) reject(error); else resolve(value);
    };
    const stop = (error: Error) => {
      child.kill();
      finish(error);
    };
    const abort = () => stop(abortError());
    const timer = setTimeout(() => stop(new Error(`${command} timed out after ${MEDIA_ANALYSIS_TIMEOUT_MS / 1000} seconds`)), MEDIA_ANALYSIS_TIMEOUT_MS);
    timer.unref();
    signal?.addEventListener("abort", abort, { once: true });
    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > outputLimit) return stop(new Error(`${command} output exceeded ${outputLimit} bytes`));
      stdout.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      if (Buffer.byteLength(stderr) < PROBE_OUTPUT_LIMIT) stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => finish(new Error(`Could not start ${command}: ${error.message}`)));
    child.on("close", (code) => code === 0
      ? finish(undefined, Buffer.concat(stdout).toString(binary ? "latin1" : "utf8"))
      : finish(new Error(stderr.trim() || `${command} failed with code ${code}`)));
  });
}

export class MediaAnalysisCoordinator {
  private current: AbortController | null = null;
  async analyze(inputPath: string): Promise<MediaAnalysis> {
    this.current?.abort();
    const controller = new AbortController();
    this.current = controller;
    try {
      return await analyzeMedia(inputPath, controller.signal);
    } finally {
      if (this.current === controller) this.current = null;
    }
  }
  cancel(): void { this.current?.abort(); this.current = null; }
}

function abortError(): Error { return new DOMException("Media analysis was superseded", "AbortError"); }
function isAbort(error: unknown): boolean { return error instanceof DOMException && error.name === "AbortError"; }
