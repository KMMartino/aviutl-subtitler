import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { AcquiredSource, SourceRange } from "../renderer/lib/types";
import { probeWebAsset, stageSourceMedia, type YtDlpInvocation } from "./webAssetAcquisition";

/** Acquire a creator's source recording, independently of media-library admission. */
export async function acquireSource(
  invocation: YtDlpInvocation, sourceRoot: string, url: string, signal?: AbortSignal,
  onProgress?: (percent: number) => void,
  range?: SourceRange,
): Promise<AcquiredSource> {
  const probe = await probeWebAsset(invocation, url, signal);
  if (probe.durationSec === null || probe.durationSec <= 0) {
    throw new Error("Use a finished recording with a known duration.");
  }
  const sourceStartSec = range?.startSec ?? 0;
  const sourceEndSec = range?.endSec ?? probe.durationSec;
  if (!Number.isFinite(sourceStartSec) || !Number.isFinite(sourceEndSec) || sourceStartSec < 0 || sourceEndSec <= sourceStartSec || sourceEndSec > probe.durationSec) {
    throw new Error("Choose a non-empty download range within the recording.");
  }
  const downloaded = await stageSourceMedia(invocation, sourceRoot, probe.sourceUrl,
    range ? { sourceStartSec, sourceEndSec } : undefined, signal, onProgress);
  const title = probe.title.normalize("NFKD").replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^[_ .]+|[_ .]+$/g, "").slice(0, 120);
  const localPath = path.join(path.dirname(downloaded.stagedPath), `recording-${title || "source"}${path.extname(downloaded.stagedPath)}`);
  fs.renameSync(downloaded.stagedPath, localPath);
  const source: AcquiredSource = {
    type: "source_media", schemaVersion: 1, revisionId: crypto.randomUUID(),
    path: localPath, title: probe.title, origin: probe,
    sourceStartSec: downloaded.sourceStartSec, sourceEndSec: downloaded.sourceEndSec ?? probe.durationSec,
  };
  const manifest = path.join(path.dirname(source.path), "source.json");
  const temporary = `${manifest}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(source, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, manifest);
  return source;
}
