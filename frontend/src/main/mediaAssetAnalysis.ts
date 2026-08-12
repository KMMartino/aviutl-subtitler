import { spawn } from "node:child_process";
import type {
  MediaAssetAnalysisEstimate,
  MediaAssetAnalysisPayload,
  MediaAssetDetail,
  MediaAssetSummary,
  MediaAnalysisDetail,
  MediaAnalysisScope,
} from "../renderer/lib/types";

const RESULT_PREFIX = "@@SUBUTL_MEDIA_ANALYSIS@@";
const MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
const DETAIL_MULTIPLIERS: Record<Exclude<MediaAnalysisDetail, "probe">, number> = {
  simple: 1,
  medium: 2,
  detailed: 5,
  precise: 10,
};
const PROBE_BREAKPOINT_SEC = 5 * 60;
const MAX_COARSE_PROBES = 100;
const MAX_EXPECTED_BOUNDARIES = 8;
const MIN_TRANSITION_BUDGET = 16;
const MAX_TRANSITION_BUDGET = 96;
const SECONDS_PER_TRANSITION = 150;

export function estimateMediaAssetAnalysis(
  asset: MediaAssetSummary,
  model: string,
  detail: MediaAnalysisDetail,
  scope?: MediaAnalysisScope,
): MediaAssetAnalysisEstimate {
  const durationSec = scope
    ? Math.max(0, scope.endMs - scope.startMs) / 1000
    : (asset.durationMs ?? 0) / 1000;
  const plan = asset.mediaKind === "image"
    ? { sampleCount: 1, maximumSampleCount: 1, coarseSampleCount: 1, maximumTransitionCount: 0, adaptive: false, breakpointPrecisionSec: null, requestCount: 1 }
    : mediaAnalysisSamplingPlan(durationSec, detail);
  const sampleCount = plan.sampleCount;
  // Low-detail images are heavily compressed by the Responses API. These
  // calibrated figures track observed billing and make frame count the main
  // variable instead of charging every run for a large fictional output.
  const inputTokens = 600 * plan.requestCount + sampleCount * 85;
  const outputTokens = 80 * plan.requestCount + sampleCount * 35;
  const rates = modelRates(model);
  return {
    assetId: asset.id,
    model,
    detail,
    recommended: detail === recommendedMediaAnalysisDetail(
      scope ? { ...asset, durationMs: scope.endMs - scope.startMs } : asset,
    ),
    sampleCount,
    maximumSampleCount: plan.maximumSampleCount,
    coarseSampleCount: plan.coarseSampleCount,
    maximumTransitionCount: plan.maximumTransitionCount,
    adaptive: plan.adaptive,
    breakpointPrecisionSec: plan.breakpointPrecisionSec,
    estimatedCostUsd: inputTokens * rates.input / 1_000_000 + outputTokens * rates.output / 1_000_000,
    privacyNotice: plan.adaptive
      ? `About ${sampleCount} frames are expected: ${plan.coarseSampleCount} survey probes, then bounded refinement only at meaningful transitions. The original media file is not uploaded.`
      : `${sampleCount} sampled frame${sampleCount === 1 ? "" : "s"} will be sent to OpenAI. The original media file is not uploaded.`,
  };
}

export function mediaAnalysisSamplingPlan(
  durationSec: number,
  detail: MediaAnalysisDetail,
): { sampleCount: number; maximumSampleCount: number; coarseSampleCount: number; maximumTransitionCount: number; adaptive: boolean; breakpointPrecisionSec: number | null; requestCount: number } {
  if (!Number.isFinite(durationSec) || durationSec <= 0) {
    return { sampleCount: 1, maximumSampleCount: 1, coarseSampleCount: 1, maximumTransitionCount: 0, adaptive: false, breakpointPrecisionSec: null, requestCount: 1 };
  }
  if (detail !== "probe") {
    const sampleCount = Math.max(1, Math.round(baseStandardFrameCount(durationSec) * DETAIL_MULTIPLIERS[detail]));
    return { sampleCount, maximumSampleCount: sampleCount, coarseSampleCount: sampleCount, maximumTransitionCount: 16, adaptive: false, breakpointPrecisionSec: null, requestCount: 1 };
  }
  const coarseSampleCount = probeFrameCount(durationSec);
  const breakpointPrecisionSec = Math.min(3, Math.max(.25, durationSec / 400));
  const coarseSpacingSec = durationSec / Math.max(1, coarseSampleCount - 1);
  const refinementRounds = Math.max(
    0,
    Math.ceil(Math.log(coarseSpacingSec / breakpointPrecisionSec) / Math.log(3)),
  );
  const expectedBoundaries = Math.min(MAX_EXPECTED_BOUNDARIES, Math.max(1, Math.ceil(durationSec / 900)));
  const maximumTransitionCount = mediaAnalysisTransitionBudget(durationSec);
  return {
    sampleCount: coarseSampleCount + 2 * refinementRounds * expectedBoundaries,
    maximumSampleCount: coarseSampleCount + 2 * refinementRounds * maximumTransitionCount,
    coarseSampleCount,
    maximumTransitionCount,
    adaptive: true,
    breakpointPrecisionSec,
    requestCount: 1 + refinementRounds,
  };
}

export function mediaAnalysisTransitionBudget(durationSec: number): number {
  if (!Number.isFinite(durationSec) || durationSec <= 0) return MIN_TRANSITION_BUDGET;
  return Math.min(
    MAX_TRANSITION_BUDGET,
    Math.max(MIN_TRANSITION_BUDGET, Math.ceil(durationSec / SECONDS_PER_TRANSITION)),
  );
}

export function recommendedMediaAnalysisDetail(asset: MediaAssetSummary): MediaAnalysisDetail {
  if (asset.mediaKind === "image") return "simple";
  const durationSec = (asset.durationMs ?? 0) / 1000;
  if (durationSec > PROBE_BREAKPOINT_SEC) return "probe";
  if (durationSec <= 10) return "precise";
  if (durationSec <= 120) return "detailed";
  return "medium";
}

function baseStandardFrameCount(durationSec: number): number {
  if (durationSec <= 2) return 1;
  if (durationSec <= 120) return interpolate(durationSec, 2, 1, 120, 10);
  if (durationSec <= 1200) return interpolate(durationSec, 120, 10, 1200, 40);
  return 40 * durationSec / 1200;
}

function probeFrameCount(durationSec: number): number {
  if (durationSec <= 2) return 4;
  if (durationSec <= 120) return Math.round(interpolate(durationSec, 2, 4, 120, 10));
  if (durationSec <= 1200) return Math.round(interpolate(durationSec, 120, 10, 1200, 40));
  return Math.min(MAX_COARSE_PROBES, Math.round(40 * Math.sqrt(durationSec / 1200)));
}

function interpolate(value: number, x1: number, y1: number, x2: number, y2: number): number {
  return y1 + (value - x1) / (x2 - x1) * (y2 - y1);
}

export async function runMediaAssetAnalysis(
  pythonPath: string,
  asset: MediaAssetDetail,
  model: string,
  detail: MediaAnalysisDetail,
  envFile: string,
  ffmpegPath: string,
  cwd: string,
  scope?: MediaAnalysisScope,
  signal?: AbortSignal,
): Promise<MediaAssetAnalysisPayload> {
  const args = [
    "-m", "subtitler.media_analysis",
    "--asset", asset.canonicalPath,
    "--kind", asset.mediaKind,
    "--ffmpeg", ffmpegPath,
    "--model", model,
    "--detail", detail,
    "--env-file", envFile,
  ];
  if (asset.durationMs !== null) args.push("--duration-sec", String(asset.durationMs / 1000));
  if (scope) {
    args.push("--start-sec", String(scope.startMs / 1000));
    args.push("--end-sec", String(scope.endMs / 1000));
  }
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error("Media analysis was cancelled."));
      return;
    }
    const child = spawn(pythonPath, args, { cwd, windowsHide: true });
    let stdout = "";
    let stderr = "";
    let bytes = 0;
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      action();
    };
    const abort = () => {
      child.kill();
      finish(() => reject(new Error("Media analysis was cancelled.")));
    };
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => {
      child.kill();
      finish(() => reject(new Error("Media analysis timed out.")));
    }, 10 * 60_000);
    child.stdout.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > MAX_OUTPUT_BYTES) child.kill();
      else stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > MAX_OUTPUT_BYTES) child.kill();
      else stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => finish(() => reject(new Error(`Could not start media analysis: ${error.message}`))));
    child.on("close", (code) => finish(() => {
      if (bytes > MAX_OUTPUT_BYTES) {
        reject(new Error("Media analysis produced too much output."));
        return;
      }
      const line = stdout.split(/\r?\n/).reverse().find((value: string) => value.startsWith(RESULT_PREFIX));
      if (!line) {
        reject(new Error(stderr.trim() || `Media analysis exited with code ${code}.`));
        return;
      }
      try {
        const payload = JSON.parse(line.slice(RESULT_PREFIX.length)) as MediaAssetAnalysisPayload & { error?: string };
        if (payload.error) reject(new Error(payload.error));
        else if (code !== 0) reject(new Error(stderr.trim() || `Media analysis exited with code ${code}.`));
        else resolve(payload);
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    }));
  });
}

function modelRates(model: string): { input: number; output: number } {
  if (model === "gpt-5.6-luna") return { input: 1, output: 6 };
  if (model === "gpt-5.6-terra") return { input: 2.5, output: 15 };
  if (model === "gpt-5.4-mini") return { input: .75, output: 4.5 };
  return { input: 5, output: 30 };
}
