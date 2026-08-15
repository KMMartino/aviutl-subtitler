import { describe, expect, it } from "vitest";
import type { MediaAssetDetail } from "../renderer/lib/types";
import { estimateMediaAssetAnalysis, mediaAnalysisSamplingPlan, mediaAnalysisTransitionBudget, recommendedMediaAnalysisDetail } from "./mediaAssetAnalysis";

describe("media asset analysis estimate", () => {
  it("bounds video sampling and reports the hosted privacy boundary", () => {
    const estimate = estimateMediaAssetAnalysis(asset({ durationMs: 3_600_000 }), "gpt-5.6-terra", "probe");
    expect(estimate.coarseSampleCount).toBe(69);
    expect(estimate.sampleCount).toBe(93);
    expect(estimate.adaptive).toBe(true);
    expect(estimate.breakpointPrecisionSec).toBe(3);
    expect(estimate.estimatedCostUsd).toBeGreaterThan(0);
    expect(estimate.privacyNotice).toContain("original media file is not uploaded");
  });

  it("uses one sample for an image", () => {
    const estimate = estimateMediaAssetAnalysis(asset({ mediaKind: "image", durationMs: null }), "gpt-5.6-luna", "detailed");
    expect(estimate.sampleCount).toBe(1);
    expect(estimate.estimatedCostUsd).toBeCloseTo(.000275);
  });

  it("scales temporal coverage and billed cost with the selected frame count", () => {
    const video = asset({ durationMs: 120_000 });
    const estimates = (["simple", "medium", "detailed", "precise"] as const)
      .map((detail) => estimateMediaAssetAnalysis(video, "gpt-5.6-terra", detail));
    expect(estimates.map((estimate) => estimate.sampleCount)).toEqual([10, 20, 50, 100]);
    expect(estimates.map((estimate) => estimate.estimatedCostUsd)).toEqual(
      [...estimates].sort((left, right) => left.sampleCount - right.sampleCount)
        .map((estimate) => estimate.estimatedCostUsd),
    );
  });

  it("estimates a selected range from its duration rather than the whole file", () => {
    const video = asset({ durationMs: 3_600_000 });
    const whole = estimateMediaAssetAnalysis(video, "gpt-5.6-terra", "detailed");
    const range = estimateMediaAssetAnalysis(
      video,
      "gpt-5.6-terra",
      "detailed",
      { startMs: 25_000, endMs: 100_000 },
    );
    expect(range.sampleCount).toBeLessThan(whole.sampleCount);
    expect(range.estimatedCostUsd).toBeLessThan(whole.estimatedCostUsd);
    expect(range.recommended).toBe(true);
  });

  it("biases two-second clips toward denser analysis by detail level", () => {
    const video = asset({ durationMs: 2_000 });
    const estimates = (["simple", "medium", "detailed", "precise"] as const)
      .map((detail) => estimateMediaAssetAnalysis(video, "gpt-5.6-terra", detail));
    expect(estimates.map((estimate) => estimate.sampleCount)).toEqual([1, 2, 5, 10]);
  });

  it("hits the 20-minute anchors and continues normal modes proportionally", () => {
    const atTwentyMinutes = (["simple", "medium", "detailed", "precise"] as const)
      .map((detail) => mediaAnalysisSamplingPlan(1200, detail).sampleCount);
    const atFortyMinutes = (["simple", "medium", "detailed", "precise"] as const)
      .map((detail) => mediaAnalysisSamplingPlan(2400, detail).sampleCount);
    expect(atTwentyMinutes).toEqual([40, 80, 200, 400]);
    expect(atFortyMinutes).toEqual([80, 160, 400, 800]);
  });

  it("uses a bounded adaptive Probe survey for a 100-minute stream", () => {
    const video = asset({ durationMs: 6_000_000 });
    const estimate = estimateMediaAssetAnalysis(video, "gpt-5.6-terra", "probe");
    expect(estimate.coarseSampleCount).toBe(89);
    expect(estimate.sampleCount).toBe(131);
    expect(estimate.maximumSampleCount).toBe(329);
    expect(estimate.maximumTransitionCount).toBe(40);
    expect(estimate.breakpointPrecisionSec).toBe(3);
    expect(estimate.adaptive).toBe(true);
    expect(estimate.recommended).toBe(true);
  });

  it("recommends dense standard analysis for short clips and Probe for long videos", () => {
    expect(recommendedMediaAnalysisDetail(asset({ durationMs: 2_000 }))).toBe("precise");
    expect(recommendedMediaAnalysisDetail(asset({ durationMs: 120_000 }))).toBe("detailed");
    expect(recommendedMediaAnalysisDetail(asset({ durationMs: 240_000 }))).toBe("medium");
    expect(recommendedMediaAnalysisDetail(asset({ durationMs: 301_000 }))).toBe("probe");
    expect(recommendedMediaAnalysisDetail(asset({ mediaKind: "image", durationMs: null }))).toBe("simple");
  });

  it("scales the Probe transition budget with duration and caps extreme streams", () => {
    expect(mediaAnalysisTransitionBudget(120)).toBe(16);
    expect(mediaAnalysisTransitionBudget(6000)).toBe(40);
    expect(mediaAnalysisTransitionBudget(10_800)).toBe(72);
    expect(mediaAnalysisTransitionBudget(20_000)).toBe(96);
  });
});

function asset(overrides: Partial<MediaAssetDetail>): MediaAssetDetail {
  return {
    id: "asset",
    rootId: "root",
    canonicalPath: "C:\\media\\asset.mp4",
    relativePath: "asset.mp4",
    relativeDirectory: "",
    mediaKind: "video",
    sourceKind: "local",
    availability: "active",
    analysisState: "metadata_only",
    title: "Asset",
    effectiveDescription: "",
    userDescription: "",
    aiDescription: "",
    inferredDescription: "",
    tags: [],
    sizeBytes: 1,
    durationMs: 60_000,
    width: 1920,
    height: 1080,
    videoCodec: "h264",
    audioCodec: "aac",
    hasAudio: true,
    transparency: "unsupported",
    mtimeNs: "1",
    quickFingerprint: "hash",
    lastSeenAt: "",
    updatedAt: "",
    sourceUrl: "",
    sourcePageUrl: "",
    creator: "",
    licenseText: "",
    acquiredAt: "",
    segments: [],
    ...overrides,
  };
}
