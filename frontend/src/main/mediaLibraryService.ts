import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Worker } from "node:worker_threads";
import type {
  MediaAssetDetail,
  MediaAssetListRequest,
  MediaAssetListResult,
  MediaAssetAnalysisEstimate,
  MediaAssetAnalysisResult,
  MediaAssetKind,
  MediaAssetSummary,
  MediaAnalysisDetail,
  MediaAnalysisScope,
  MediaBulkAnalysisPlan,
  MediaLibraryRoot,
  MediaLibraryDirectory,
  MediaLibraryRootKind,
  MediaLibraryRootPurpose,
  MediaLibraryScanResult,
  WebAssetAcquireRequest,
  WebAssetProbe,
} from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";
import { resolveFfmpegCommand } from "./ffmpegManager";
import { enumerateMediaDirectories, enumerateMediaFiles, inspectMediaFile } from "./mediaLibraryScanner";
import { probeWebAsset, promoteStagedAsset, stageWebAsset, type YtDlpInvocation } from "./webAssetAcquisition";
import { estimateMediaAssetAnalysis, runMediaAssetAnalysis } from "./mediaAssetAnalysis";
import { ensureMediaThumbnail } from "./mediaLibraryThumbnails";

const MAX_SCAN_ASSETS = 10_000;
const ANALYSIS_DETAILS: MediaAnalysisDetail[] = ["simple", "medium", "detailed", "precise", "probe"];

type PendingRequest = {
  resolve(value: unknown): void;
  reject(error: Error): void;
};

export class MediaLibraryService {
  private worker: Worker | null = null;
  private requestId = 0;
  private readonly pending = new Map<number, PendingRequest>();
  private readonly activeScans = new Map<string, Promise<MediaLibraryScanResult>>();
  private readonly activeAnalyses = new Map<string, AbortController>();

  constructor(private readonly paths: RuntimePaths) {}

  async initialize(): Promise<void> {
    if (this.worker) return;
    fs.mkdirSync(this.paths.mediaLibraryRoot, { recursive: true });
    fs.mkdirSync(this.paths.managedMediaRoot, { recursive: true });
    fs.mkdirSync(this.paths.managedWebMediaRoot, { recursive: true });
    fs.mkdirSync(path.join(this.paths.mediaLibraryRoot, "web-staging"), { recursive: true });
    fs.mkdirSync(path.join(this.paths.mediaLibraryRoot, "thumbnails"), { recursive: true });
    const worker = new Worker(path.join(__dirname, "mediaLibraryWorker.js"), {
      workerData: { databasePath: this.paths.mediaLibraryDatabase },
    });
    worker.on("message", (message: { id: number; result?: unknown; error?: string }) => {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error));
      else pending.resolve(message.result);
    });
    worker.on("error", (error) => this.rejectAll(error));
    worker.on("exit", (code) => {
      this.worker = null;
      if (code !== 0) this.rejectAll(new Error(`Media library worker exited with code ${code}.`));
    });
    this.worker = worker;
    await this.addRoot(this.paths.managedMediaRoot, "managed", false, "generated");
    await this.addRoot(this.paths.managedWebMediaRoot, "managed", false, "web");
  }

  async close(): Promise<void> {
    const worker = this.worker;
    if (!worker) return;
    try {
      await this.call("close");
    } finally {
      await worker.terminate();
      this.worker = null;
    }
  }

  listRoots(): Promise<MediaLibraryRoot[]> {
    return this.call("listRoots");
  }

  async addRoot(
    rootPath: string,
    kind: MediaLibraryRootKind = "referenced",
    recursive = false,
    purpose: MediaLibraryRootPurpose = kind === "managed" ? "generated" : "user",
  ): Promise<MediaLibraryRoot> {
    const stat = fs.statSync(rootPath);
    if (!stat.isDirectory()) throw new Error("Media library root must be a directory.");
    return this.call("addRoot", path.resolve(rootPath), kind, recursive, purpose);
  }

  setRootEnabled(rootId: string, enabled: boolean): Promise<MediaLibraryRoot> {
    return this.call("setRootEnabled", rootId, enabled);
  }

  async removeRoot(rootId: string): Promise<{ removedAssets: number }> {
    if (this.activeScans.has(rootId)) throw new Error("Wait for this media-location scan to finish before removing it.");
    return this.call("removeRoot", rootId);
  }

  async listDirectories(rootId: string): Promise<MediaLibraryDirectory[]> {
    const root = (await this.listRoots()).find((item) => item.id === rootId);
    if (!root) throw new Error("Media library root was not found.");
    const [directories, scopes, tracked, visibility, hiddenStates] = await Promise.all([
      enumerateMediaDirectories(root.canonicalPath),
      this.call<string[]>("listDirectoryScopes", rootId),
      this.call<Record<string, number>>("directoryTrackedCounts", rootId),
      this.call<Array<{ relativeDirectory: string; kind: "subtree" | "direct"; visible: boolean }>>(
        "directoryVisibility",
        rootId,
      ),
      this.call<Array<{ relativeDirectory: string; hidden: boolean }>>("directoryHiddenStates", rootId),
    ]);
    const included = new Set(scopes);
    const visibilityMap = new Map(
      visibility.map((item) => [`${item.kind}:${item.relativeDirectory}`, item.visible]),
    );
    const hiddenMap = new Map(hiddenStates.map((item) => [item.relativeDirectory, item.hidden]));
    const subtreeTracked = new Map<string, number>();
    for (const [relativeDirectory, count] of Object.entries(tracked)) {
      for (const ancestor of directoryAncestors(relativeDirectory)) {
        subtreeTracked.set(ancestor, (subtreeTracked.get(ancestor) ?? 0) + count);
      }
    }
    return directories.map((directory) => ({
      rootId,
      relativePath: directory.relativePath,
      name: directory.name,
      depth: directory.depth,
      directFileCount: directory.directFileCount,
      trackedFileCount: tracked[directory.relativePath] ?? 0,
      subtreeTrackedFileCount: subtreeTracked.get(directory.relativePath) ?? 0,
      included: included.has(directory.relativePath),
      subtreeEnabled: visibilityMap.get(`subtree:${directory.relativePath}`) !== false,
      directEnabled: visibilityMap.get(`direct:${directory.relativePath}`) !== false,
      visible: root.enabled && directoryAncestors(directory.relativePath)
        .every((ancestor) => visibilityMap.get(`subtree:${ancestor}`) !== false),
      directFilesVisible: root.enabled
        && directoryAncestors(directory.relativePath)
          .every((ancestor) => visibilityMap.get(`subtree:${ancestor}`) !== false)
        && visibilityMap.get(`direct:${directory.relativePath}`) !== false,
      hidden: hiddenMap.get(directory.relativePath) === true,
      managed: root.kind === "managed",
      purpose: root.purpose,
    }));
  }

  async setDirectoryVisible(
    rootId: string,
    relativeDirectory: string,
    kind: "subtree" | "direct",
    visible: boolean,
  ): Promise<void> {
    await this.call("setDirectoryVisible", rootId, relativeDirectory, kind, visible);
  }

  async setDirectoryHidden(rootId: string, relativeDirectory: string, hidden: boolean): Promise<void> {
    await this.call("setDirectoryHidden", rootId, relativeDirectory, hidden);
  }

  async setDirectoryIncluded(rootId: string, relativeDirectory: string, included: boolean): Promise<void> {
    await this.call("setDirectoryIncluded", rootId, relativeDirectory, included);
    if (included) await this.scanRoot(rootId);
  }

  async removeDirectoryAssets(
    rootId: string,
    relativeDirectory: string,
    deleteFiles: boolean,
  ): Promise<{ removedAssets: number; deletedFiles: number; errors: string[] }> {
    if (this.activeScans.has(rootId)) throw new Error("Wait for this media-location scan to finish first.");
    const root = (await this.listRoots()).find((item) => item.id === rootId);
    if (!root) throw new Error("Media library root was not found.");
    if (deleteFiles && root.kind !== "managed") {
      throw new Error("Files can only be deleted from an application-managed media location.");
    }
    const result = await this.call<{ removedAssets: number; paths: string[] }>(
      "removeDirectoryAssets",
      rootId,
      relativeDirectory,
    );
    let deletedFiles = 0;
    const errors: string[] = [];
    if (deleteFiles) {
      const expectedParent = path.resolve(root.canonicalPath, relativeDirectory);
      for (const filePath of result.paths) {
        const resolved = path.resolve(filePath);
        if (path.dirname(resolved).toLocaleLowerCase() !== expectedParent.toLocaleLowerCase()) {
          errors.push(`${resolved}: path did not match the selected directory`);
          continue;
        }
        try {
          await fs.promises.rm(resolved);
          deletedFiles += 1;
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
            errors.push(`${resolved}: ${error instanceof Error ? error.message : String(error)}`);
          }
        }
      }
    }
    return { removedAssets: result.removedAssets, deletedFiles, errors };
  }

  listAssets(request: MediaAssetListRequest): Promise<MediaAssetListResult> {
    return this.call("listAssets", request);
  }

  getAsset(assetId: string): Promise<MediaAssetDetail> {
    return this.call("getAsset", assetId);
  }

  async thumbnailPaths(assetIds: string[]): Promise<Record<string, string>> {
    const uniqueIds = [...new Set(assetIds)].slice(0, 100);
    const ffmpeg = resolveFfmpegCommand("ffmpeg", this.paths);
    const thumbnailRoot = path.join(this.paths.mediaLibraryRoot, "thumbnails");
    const entries: Array<[string, string]> = [];
    await mapLimited(uniqueIds, 2, async (assetId) => {
      try {
        const asset = await this.getAsset(assetId);
        entries.push([assetId, await ensureMediaThumbnail(asset, thumbnailRoot, ffmpeg)]);
      } catch {
        entries.push([assetId, ""]);
      }
    });
    return Object.fromEntries(entries);
  }

  updateUserDescription(assetId: string, description: string): Promise<MediaAssetDetail> {
    return this.call("updateUserDescription", assetId, description.trim());
  }

  addUserSegment(assetId: string, scope: MediaAnalysisScope, description: string): Promise<MediaAssetDetail> {
    return this.call("addUserSegment", assetId, scope, description.trim());
  }

  probeWebAsset(invocation: YtDlpInvocation, sourceUrl: string): Promise<WebAssetProbe> {
    return probeWebAsset(invocation, sourceUrl);
  }

  async acquireWebAsset(invocation: YtDlpInvocation, request: WebAssetAcquireRequest): Promise<MediaAssetDetail> {
    const probe = await probeWebAsset(invocation, request.sourceUrl);
    const staged = await stageWebAsset(
      invocation,
      path.join(this.paths.mediaLibraryRoot, "web-staging"),
      request,
      probe,
    );
    const promotedPath = promoteStagedAsset(
      staged.stagedPath,
      this.paths.managedWebMediaRoot,
    );
    const managedRoot = (await this.listRoots()).find((root) => root.purpose === "web");
    if (!managedRoot) throw new Error("Managed media root is unavailable.");
    await this.scanRoot(managedRoot.id);
    const promotedCanonicalPath = path.resolve(promotedPath);
    let offset = 0;
    let asset: MediaAssetListResult["assets"][number] | undefined;
    do {
      const result = await this.listAssets({ rootId: managedRoot.id, offset, limit: 200 });
      asset = result.assets.find((item) => path.resolve(item.canonicalPath) === promotedCanonicalPath);
      if (asset || offset + result.assets.length >= result.total) break;
      offset += result.assets.length;
    } while (true);
    if (!asset) throw new Error("Downloaded media could not be indexed.");
    await this.updateUserDescription(asset.id, request.description);
    return this.call(
      "updateProvenance",
      asset.id,
      probe.sourceUrl,
      probe.sourcePageUrl,
      request.creator?.trim() || probe.creator,
      request.licenseText?.trim() || probe.licenseText,
      new Date().toISOString(),
    );
  }

  async estimateAnalysis(
    assetId: string,
    model: string,
    scope?: MediaAnalysisScope,
  ): Promise<MediaAssetAnalysisEstimate[]> {
    const asset = await this.getAsset(assetId);
    const details: MediaAnalysisDetail[] = asset.mediaKind === "image" ? ["simple"] : ANALYSIS_DETAILS;
    return details.map((detail) => estimateMediaAssetAnalysis(asset, model, detail, scope));
  }

  async bulkAnalysisPlan(mediaKind: MediaAssetKind | undefined, model: string): Promise<MediaBulkAnalysisPlan> {
    const assets = await this.call<MediaAssetSummary[]>("listAnalysisCandidates", mediaKind);
    const details: MediaAnalysisDetail[] = mediaKind === "image" || (assets.length > 0 && assets.every((asset) => asset.mediaKind === "image"))
      ? ["simple"]
      : ANALYSIS_DETAILS;
    const estimates = details.map((detail) => {
      const assetEstimates = assets.map((asset) => estimateMediaAssetAnalysis(asset, model, detail));
      return {
        detail,
        recommendedAssetCount: assetEstimates.filter((estimate) => estimate.recommended).length,
        assetCount: assets.length,
        sampleCount: assetEstimates.reduce((total, estimate) => total + estimate.sampleCount, 0),
        estimatedCostUsd: assetEstimates.reduce((total, estimate) => total + estimate.estimatedCostUsd, 0),
      };
    });
    return {
      assetIds: assets.map((asset) => asset.id),
      estimates,
      privacyNotice: "Only sampled frames will be sent to OpenAI. Original media files are not uploaded.",
    };
  }

  async analyzeAsset(
    assetId: string,
    pythonPath: string,
    model: string,
    detail: MediaAnalysisDetail,
    envFile: string,
    scope?: MediaAnalysisScope,
  ): Promise<MediaAssetAnalysisResult> {
    if (this.activeAnalyses.has(assetId)) throw new Error("This media file is already being analyzed.");
    const asset = await this.getAsset(assetId);
    const estimate = estimateMediaAssetAnalysis(asset, model, detail, scope);
    const runId = crypto.randomUUID();
    const controller = new AbortController();
    await this.call("startAnalysis", assetId, runId, "openai", model, estimate.estimatedCostUsd, detail, scope);
    this.activeAnalyses.set(assetId, controller);
    try {
      const result = await runMediaAssetAnalysis(
        pythonPath,
        asset,
        model,
        detail,
        envFile,
        resolveFfmpegCommand("ffmpeg", this.paths),
        this.paths.bundledBackendRoot,
        scope,
        controller.signal,
      );
      const updated = await this.call<MediaAssetDetail>("completeAnalysis", assetId, runId, result, scope);
      return {
        asset: updated,
        sampleCount: result.sample_count,
        inputTokens: result.input_tokens,
        outputTokens: result.output_tokens,
        costUsd: result.cost_usd,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.call("failAnalysis", assetId, runId, message);
      throw error;
    } finally {
      this.activeAnalyses.delete(assetId);
    }
  }

  cancelAnalysis(assetId: string): boolean {
    const controller = this.activeAnalyses.get(assetId);
    if (!controller) return false;
    controller.abort();
    return true;
  }

  scanRoot(rootId: string): Promise<MediaLibraryScanResult> {
    const active = this.activeScans.get(rootId);
    if (active) return active;
    const scan = this.performScan(rootId).finally(() => this.activeScans.delete(rootId));
    this.activeScans.set(rootId, scan);
    return scan;
  }

  private async performScan(rootId: string): Promise<MediaLibraryScanResult> {
    const root = (await this.listRoots()).find((item) => item.id === rootId);
    if (!root) throw new Error("Media library root was not found.");
    if (!root.enabled) throw new Error("Media library root is disabled.");
    const token = await this.call<string>("beginScan", rootId);
    const result: MediaLibraryScanResult = {
      rootId,
      discovered: 0,
      indexed: 0,
      incompatible: 0,
      missing: 0,
      errors: [],
    };
    try {
      const scopes = await this.call<string[]>("listDirectoryScopes", rootId);
      const scannedDirectories = scopes;
      const files = [];
      for (const relativeDirectory of scannedDirectories) {
        const discovered = await enumerateMediaFiles(
          path.resolve(root.canonicalPath, relativeDirectory),
          false,
          MAX_SCAN_ASSETS + 1 - files.length,
        );
        files.push(...discovered);
        if (files.length > MAX_SCAN_ASSETS) break;
      }
      result.discovered = files.length;
      if (files.length > MAX_SCAN_ASSETS) {
        throw new Error(
          `This location contains more than ${MAX_SCAN_ASSETS.toLocaleString()} supported files. `
          + "Remove frame-dump folders or add a narrower, non-recursive location.",
        );
      }
      const ffprobe = resolveFfmpegCommand("ffprobe", this.paths);
      await mapLimited(files, 2, async (item) => {
        try {
          const inspected = await inspectMediaFile(item.path, root.canonicalPath, item.kind, ffprobe);
          await this.call("upsertIndexedFile", rootId, token, inspected);
          result.indexed += 1;
          if (inspected.availability === "incompatible") result.incompatible += 1;
        } catch (error) {
          result.errors.push(`${item.path}: ${error instanceof Error ? error.message : String(error)}`);
        }
      });
      const finished = await this.call<{ missing: number }>(
        "finishScan",
        rootId,
        token,
        scannedDirectories,
        "",
      );
      result.missing = finished.missing;
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.call("finishScan", rootId, token, [], message);
      throw error;
    }
  }

  private call<T = unknown>(method: string, ...args: unknown[]): Promise<T> {
    const worker = this.worker;
    if (!worker) return Promise.reject(new Error("Media library is not initialized."));
    const id = ++this.requestId;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject });
      worker.postMessage({ id, method, args });
    });
  }

  private rejectAll(error: Error): void {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }
}

function directoryAncestors(relativeDirectory: string): string[] {
  const pending: string[] = [];
  let current = relativeDirectory;
  while (current) {
    pending.unshift(current);
    const parent = path.dirname(current);
    current = parent === "." ? "" : parent;
  }
  return ["", ...pending];
}

async function mapLimited<T>(items: T[], concurrency: number, action: (item: T) => Promise<void>): Promise<void> {
  let index = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (index < items.length) {
      const item = items[index++];
      await action(item);
    }
  });
  await Promise.all(workers);
}
