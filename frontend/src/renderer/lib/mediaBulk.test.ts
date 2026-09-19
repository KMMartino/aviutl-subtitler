import { describe, expect, it, vi } from "vitest";
import type { MediaAssetAnalysisEstimate, MediaAssetSummary } from "./types";
import { collectSelectableAssets, combineAnalysisEstimates } from "./mediaBulk";

const asset = (id: string, availability = "active", analysisState = "ready") => ({ id, availability, analysisState }) as MediaAssetSummary;
const estimate = (detail: string, sampleCount: number, estimatedCostUsd: number, recommended = false) => ({ detail, sampleCount, estimatedCostUsd, recommended }) as MediaAssetAnalysisEstimate;

describe("selected media bulk analysis", () => {
  it("selects matching assets across pages, including already analyzed files, excluding unavailable and busy files", async () => {
    const first = Array.from({ length: 200 }, (_, i) => asset(String(i)));
    const list = vi.fn().mockResolvedValueOnce({ assets: first, total: 204 })
      .mockResolvedValueOnce({ assets: [asset("new", "active", "metadata_only"), asset("missing", "missing"), asset("busy", "active", "analyzing"), asset("bad", "incompatible")], total: 204 });
    const ids = await collectSelectableAssets(list, { query: "trailer", rootId: "folder", analysisStatus: "unanalyzed" });
    expect(ids).toHaveLength(201);
    expect(ids).toContain("0");
    expect(ids).toContain("new");
    expect(list).toHaveBeenNthCalledWith(2, { query: "trailer", rootId: "folder", analysisStatus: "unanalyzed", offset: 200, limit: 200 });
  });
  it("totals only the selected files and includes single-level image estimates at each video level", () => {
    expect(combineAnalysisEstimates([
      [estimate("simple", 1, .001, true)],
      [estimate("simple", 5, .005), estimate("detailed", 25, .025, true)],
    ])).toEqual([
      { detail: "simple", assetCount: 2, recommendedAssetCount: 1, sampleCount: 6, estimatedCostUsd: .006 },
      { detail: "detailed", assetCount: 2, recommendedAssetCount: 2, sampleCount: 26, estimatedCostUsd: .026000000000000002 },
    ]);
  });
});
