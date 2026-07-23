import { protocol } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import type { RunRequest, SilenceCutCandidate } from "../renderer/lib/types";
import { resolveFfmpegCommand } from "./ffmpegManager";
import { mediaContentType, resolveByteRange } from "./mediaRange";

type PreviewVariant = "original" | "seam";
type RunPreview = {
  request: RunRequest;
  candidates: Map<string, SilenceCutCandidate>;
  sourceUrl: string;
  tokens: Set<string>;
  proxies: Map<string, string>;
  children: Set<ChildProcessWithoutNullStreams>;
};

export const SILENCE_MEDIA_SCHEME = "subutl-media";

export function registerSilenceMediaScheme(): void {
  protocol.registerSchemesAsPrivileged([{
    scheme: SILENCE_MEDIA_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
  }]);
}

export class SilencePreviewManager {
  private readonly files = new Map<string, string>();
  private readonly runs = new Map<string, RunPreview>();
  private queue: Promise<unknown> = Promise.resolve();

  constructor(private readonly cacheRoot: string) {}

  initialize(): void {
    fs.rmSync(this.cacheRoot, { recursive: true, force: true });
    fs.mkdirSync(this.cacheRoot, { recursive: true });
    protocol.handle(SILENCE_MEDIA_SCHEME, (request) => {
      const token = new URL(request.url).pathname.replace(/^\//, "");
      const file = this.files.get(token);
      if (!file || !fs.existsSync(file)) return new Response("Not found", { status: 404 });
      return streamMediaFile(file, request);
    });
  }

  registerRun(runId: string, request: RunRequest): void {
    this.cleanupRun(runId);
    const tokens = new Set<string>();
    const sourceUrl = this.registerFile(request.inputPath, tokens);
    this.runs.set(runId, { request, candidates: new Map(), sourceUrl, tokens, proxies: new Map(), children: new Set() });
  }

  setCandidates(runId: string, candidates: SilenceCutCandidate[]): void {
    const run = this.requireRun(runId);
    run.candidates = new Map(candidates.map((candidate) => [candidate.id, candidate]));
  }

  source(runId: string): { url: string } {
    return { url: this.requireRun(runId).sourceUrl };
  }

  proxy(runId: string, candidateId: string, variant: PreviewVariant): Promise<{ url: string }> {
    const run = this.requireRun(runId);
    const candidate = run.candidates.get(candidateId);
    if (!candidate) throw new Error("Unknown silence-cut candidate");
    const key = `${candidateId}:${variant}`;
    const existing = run.proxies.get(key);
    if (existing) return Promise.resolve({ url: existing });
    const task = this.queue.then(async () => {
      const current = this.requireRun(runId);
      const cached = current.proxies.get(key);
      if (cached) return { url: cached };
      const directory = path.join(this.cacheRoot, runId);
      fs.mkdirSync(directory, { recursive: true });
      const output = path.join(directory, `${candidateId}-${variant}.mp4`);
      await generateProxy(current.request, candidate, variant, output, current.children);
      if (!this.runs.has(runId)) {
        fs.rmSync(output, { force: true });
        throw new Error("Silence preview run ended");
      }
      const url = this.registerFile(output, current.tokens);
      current.proxies.set(key, url);
      this.enforceProxyLimit(current, 4);
      return { url };
    });
    this.queue = task.catch(() => undefined);
    return task;
  }

  prefetch(runId: string, candidateIds: string[]): void {
    for (const candidateId of candidateIds.slice(0, 2)) {
      void this.proxy(runId, candidateId, "original").catch(() => undefined);
      void this.proxy(runId, candidateId, "seam").catch(() => undefined);
    }
  }

  cleanupRun(runId: string): void {
    const run = this.runs.get(runId);
    if (run) {
      for (const child of run.children) child.kill();
      for (const token of run.tokens) this.files.delete(token);
    }
    this.runs.delete(runId);
    fs.rmSync(path.join(this.cacheRoot, runId), { recursive: true, force: true });
  }

  cleanupAll(): void {
    for (const runId of [...this.runs.keys()]) this.cleanupRun(runId);
    fs.rmSync(this.cacheRoot, { recursive: true, force: true });
  }

  private registerFile(file: string, tokens: Set<string>): string {
    const token = crypto.randomBytes(24).toString("hex");
    this.files.set(token, path.resolve(file));
    tokens.add(token);
    return `${SILENCE_MEDIA_SCHEME}://media/${token}`;
  }

  private requireRun(runId: string): RunPreview {
    const run = this.runs.get(runId);
    if (!run) throw new Error("No active silence preview for this run");
    return run;
  }

  private enforceProxyLimit(run: RunPreview, maximum: number): void {
    while (run.proxies.size > maximum) {
      const oldest = run.proxies.entries().next().value as [string, string] | undefined;
      if (!oldest) return;
      run.proxies.delete(oldest[0]);
      const token = new URL(oldest[1]).pathname.replace(/^\//, "");
      const file = this.files.get(token);
      if (file) fs.rmSync(file, { force: true });
      this.files.delete(token);
      run.tokens.delete(token);
    }
  }
}

function streamMediaFile(file: string, request: Request): Response {
  const size = fs.statSync(file).size;
  const range = resolveByteRange(request.headers.get("range"), size);
  const commonHeaders = { "Accept-Ranges": "bytes", "Content-Type": mediaContentType(file) };
  if (range === "unsatisfiable") {
    return new Response(null, { status: 416, headers: { ...commonHeaders, "Content-Range": `bytes */${size}` } });
  }
  const start = range?.start ?? 0;
  const end = range?.end ?? Math.max(0, size - 1);
  const headers = new Headers({ ...commonHeaders, "Content-Length": String(Math.max(0, end - start + 1)) });
  if (range) headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
  if (request.method === "HEAD" || size === 0) return new Response(null, { status: range ? 206 : 200, headers });
  const body = Readable.toWeb(fs.createReadStream(file, { start, end }));
  return new Response(body, { status: range ? 206 : 200, headers });
}

async function generateProxy(request: RunRequest, candidate: SilenceCutCandidate, variant: PreviewVariant, output: string, children: Set<ChildProcessWithoutNullStreams>): Promise<void> {
  const ffmpeg = resolveFfmpegCommand("ffmpeg");
  const contextStart = Math.max(0, candidate.cutStart - 2);
  const contextEnd = candidate.cutEnd + 2;
  const videoFilter = `fps=${request.silencePreviewFps},scale=-2:${request.silencePreviewHeight}:force_original_aspect_ratio=decrease`;
  let args: string[];
  if (variant === "original") {
    args = [
      "-y", "-v", "error", "-ss", String(contextStart), "-to", String(contextEnd), "-i", request.inputPath,
      "-map", "0:v:0", "-map", `0:a:${request.audioTrack ?? 0}`, "-vf", videoFilter,
    ];
  } else {
    const filter = [
      `[0:v:0]trim=start=${contextStart}:end=${candidate.cutStart},setpts=PTS-STARTPTS[v0]`,
      `[0:v:0]trim=start=${candidate.cutEnd}:end=${contextEnd},setpts=PTS-STARTPTS[v1]`,
      `[v0][v1]concat=n=2:v=1:a=0,${videoFilter}[vout]`,
      `[0:a:${request.audioTrack ?? 0}]atrim=start=${contextStart}:end=${candidate.cutStart},asetpts=PTS-STARTPTS[a0]`,
      `[0:a:${request.audioTrack ?? 0}]atrim=start=${candidate.cutEnd}:end=${contextEnd},asetpts=PTS-STARTPTS[a1]`,
      "[a0][a1]concat=n=2:v=0:a=1[aout]",
    ].join(";");
    args = ["-y", "-v", "error", "-i", request.inputPath, "-filter_complex", filter, "-map", "[vout]", "-map", "[aout]"];
  }
  args.push("-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output);
  await runFfmpeg(ffmpeg, args, children);
}

function runFfmpeg(command: string, args: string[], children: Set<ChildProcessWithoutNullStreams>): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    children.add(child);
    let stderr = "";
    child.stderr.on("data", (chunk: Buffer) => { if (stderr.length < 1_000_000) stderr += chunk.toString("utf8"); });
    child.on("error", (error) => { children.delete(child); reject(new Error(`Could not start preview FFmpeg: ${error.message}`)); });
    child.on("close", (code) => { children.delete(child); if (code === 0) resolve(); else reject(new Error(stderr.trim() || `Preview FFmpeg failed with code ${code}`)); });
  });
}
