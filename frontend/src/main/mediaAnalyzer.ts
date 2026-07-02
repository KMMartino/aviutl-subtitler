import { spawn } from "node:child_process";
import type { AudioTrackInfo, MediaAnalysis } from "../renderer/lib/types";

type ProbeData = {
  format?: { duration?: string; format_name?: string };
  streams?: Array<{
    index?: number;
    codec_type?: string;
    codec_name?: string;
    width?: number;
    height?: number;
    sample_rate?: string;
    channels?: number;
    channel_layout?: string;
    tags?: { language?: string; title?: string };
  }>;
};

export async function analyzeMedia(inputPath: string): Promise<MediaAnalysis> {
  const data = JSON.parse(await runText(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration,format_name:stream=index,codec_type,codec_name,width,height,sample_rate,channels,channel_layout:stream_tags=language,title", "-of", "json", inputPath]
  )) as ProbeData;
  const video = (data.streams ?? []).find((stream) => stream.codec_type === "video");
  const audioTracks: AudioTrackInfo[] = (data.streams ?? [])
    .filter((stream) => stream.codec_type === "audio")
    .map((stream, audioIndex) => ({
      audioIndex,
      streamIndex: Number(stream.index ?? audioIndex),
      codec: String(stream.codec_name ?? "unknown"),
      sampleRate: stream.sample_rate ? Number(stream.sample_rate) : null,
      channels: stream.channels ?? null,
      channelLayout: String(stream.channel_layout ?? ""),
      language: String(stream.tags?.language ?? ""),
      title: String(stream.tags?.title ?? "")
    }));
  const thumbnailDataUrl = video ? await createThumbnail(inputPath).catch(() => "") : "";
  return {
    durationSeconds: data.format?.duration ? Number(data.format.duration) : null,
    formatName: String(data.format?.format_name ?? ""),
    videoCodec: String(video?.codec_name ?? ""),
    width: video?.width ?? null,
    height: video?.height ?? null,
    thumbnailDataUrl,
    audioTracks
  };
}

function createThumbnail(inputPath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("ffmpeg", [
      "-v", "error", "-ss", "0", "-i", inputPath, "-map", "0:v:0",
      "-frames:v", "1", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
      "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"
    ], { windowsHide: true });
    const chunks: Buffer[] = [];
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString("utf8"); });
    child.on("error", (error) => reject(new Error(`Could not start ffmpeg: ${error.message}`)));
    child.on("exit", (code) => {
      const image = Buffer.concat(chunks);
      if (code !== 0 || !image.length) reject(new Error(stderr.trim() || "Could not extract video thumbnail"));
      else resolve(`data:image/jpeg;base64,${image.toString("base64")}`);
    });
  });
}

function runText(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString("utf8"); });
    child.on("error", (error) => reject(new Error(`Could not start ${command}: ${error.message}`)));
    child.on("exit", (code) => code === 0 ? resolve(stdout) : reject(new Error(stderr.trim() || `${command} failed`)));
  });
}
