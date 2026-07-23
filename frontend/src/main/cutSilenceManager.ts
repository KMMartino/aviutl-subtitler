import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { CutSilenceEncoderPreset, EncoderProbeResult } from "../renderer/lib/types";
import { resolveFfmpegCommand } from "./ffmpegManager";

type ConfiguredPreset = Exclude<CutSilenceEncoderPreset, "unconfigured">;

export const CUT_SILENCE_PRESETS: Array<{ id: ConfiguredPreset; label: string; args: string[] }> = [
  { id: "hevc-amf-cqp21", label: "AMD HEVC (AMF, CQP 21)", args: ["-c:v", "hevc_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "21", "-qp_p", "21", "-qp_b", "21", "-g", "60"] },
  { id: "hevc-nvenc-qp21", label: "NVIDIA HEVC (NVENC, QP 21)", args: ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "constqp", "-qp", "21", "-g", "60"] },
  { id: "hevc-qsv-q21", label: "Intel HEVC (Quick Sync, Q 21)", args: ["-c:v", "hevc_qsv", "-preset", "slow", "-global_quality", "21", "-g", "60"] },
  { id: "libx265-crf21", label: "CPU HEVC (x265, CRF 21)", args: ["-c:v", "libx265", "-preset", "medium", "-crf", "21", "-g", "60"] },
];

export async function probeCutSilenceEncoders(): Promise<EncoderProbeResult[]> {
  const ffmpeg = resolveFfmpegCommand("ffmpeg");
  const results: EncoderProbeResult[] = [];
  for (const preset of CUT_SILENCE_PRESETS) {
    const output = path.join(os.tmpdir(), `subutl-encoder-probe-${process.pid}-${preset.id}.mkv`);
    fs.rmSync(output, { force: true });
    try {
      await runProbe(ffmpeg, [
        "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=128x72:r=30",
        "-frames:v", "2", ...preset.args, "-pix_fmt", "yuv420p", "-an", output,
      ]);
      results.push({ preset: preset.id, label: preset.label, available: true, error: "" });
    } catch (error) {
      results.push({ preset: preset.id, label: preset.label, available: false, error: error instanceof Error ? error.message : String(error) });
    } finally {
      fs.rmSync(output, { force: true });
    }
  }
  return results;
}

function runProbe(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("Encoder probe timed out"));
    }, 15_000);
    timer.unref();
    child.stderr.on("data", (chunk: Buffer) => { if (stderr.length < 16_384) stderr += chunk.toString("utf8"); });
    child.on("error", (error) => { clearTimeout(timer); reject(new Error(`Could not start encoder probe: ${error.message}`)); });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(stderr.trim().split(/\r?\n/).slice(-3).join(" ") || `FFmpeg exited with code ${code}`));
    });
  });
}
