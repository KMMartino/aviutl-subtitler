import type { MediaAssetAnalysisEstimate, MediaAssetListRequest, MediaAssetListResult, MediaAssetSummary, MediaBulkAnalysisPlan } from "./types";

export function canAnalyzeAsset(asset: MediaAssetSummary): boolean {
  return asset.availability === "active" && asset.analysisState !== "analyzing";
}

export async function collectSelectableAssets(list: (request: MediaAssetListRequest) => Promise<MediaAssetListResult>, filter: MediaAssetListRequest): Promise<string[]> {
  const ids = new Set<string>();
  for (let offset = 0; ; offset += 200) {
    const page = await list({ ...filter, offset, limit: 200 });
    for (const asset of page.assets) if (canAnalyzeAsset(asset)) ids.add(asset.id);
    if (!page.assets.length || offset + page.assets.length >= page.total) return [...ids];
  }
}

export function combineAnalysisEstimates(groups: MediaAssetAnalysisEstimate[][]): MediaBulkAnalysisPlan["estimates"] {
  const details = [...new Set(groups.flatMap((group) => group.map((estimate) => estimate.detail)))];
  return details.map((detail) => {
    // Image analysis has only a simple estimate, regardless of the chosen video sampling level.
    const estimates = groups.map((group) => group.find((estimate) => estimate.detail === detail) ?? group[0]);
    return { detail, assetCount: groups.length,
      recommendedAssetCount: estimates.filter((estimate) => estimate?.recommended).length,
      sampleCount: estimates.reduce((sum, estimate) => sum + (estimate?.sampleCount ?? 0), 0),
      estimatedCostUsd: estimates.reduce((sum, estimate) => sum + (estimate?.estimatedCostUsd ?? 0), 0) };
  });
}
