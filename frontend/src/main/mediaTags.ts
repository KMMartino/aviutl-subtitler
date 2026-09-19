import crypto from "node:crypto";
import path from "node:path";
import type { DatabaseSync } from "node:sqlite";
import { normalizeTagValue, parseMediaTag, type MediaTag, type TagFilter } from "../shared/mediaTags";

type Row = Record<string, unknown>;
// Automatic tags describe searchable content, never a bag of words from prose.
const genericTags = new Set("colorful|interior|exterior|atmosphere|dramatic|cinematic imagery|imagery|details|emphasis|best|polished|retro-futurist|lived-in|game|announcement|video|image|scene|footage|ゲーム|カラフル|室内|屋内|雰囲気|映像|詳細".split("|"));
const automaticCategories = new Set(["game", "platform", "subject", "action", "category", "keyword"]);

export function selectSearchTags(values: string[]): TagFilter[] {
  const parsed: TagFilter[] = [];
  for (const value of values) {
    try {
      const tag = parseMediaTag(value);
      if (automaticCategories.has(tag.category) && !genericTags.has(normalizeTagValue(tag.value))) parsed.push(tag);
    } catch { /* Ignore malformed model tags. */ }
  }
  // Prefer typed identities and complete phrases over untyped duplicates/fragments.
  parsed.sort((a, b) => Number(a.category === "keyword") - Number(b.category === "keyword"));
  return parsed.filter((tag, index) => {
    const value = normalizeTagValue(tag.value);
    return !parsed.some((other, otherIndex) => {
      const candidate = normalizeTagValue(other.value);
      return candidate === value ? otherIndex < index
        : tag.category === "keyword" && ` ${candidate} `.includes(` ${value} `);
    });
  }).slice(0, 8);
}

export class MediaTagIndex {
  constructor(private readonly db: DatabaseSync) {}

  initialize(): boolean {
    const existing = this.db.prepare("SELECT 1 FROM sqlite_master WHERE name='media_tags'").get();
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS media_tags (
        id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        segment_id TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL, value TEXT NOT NULL, normalized_value TEXT NOT NULL,
        origin TEXT NOT NULL CHECK(origin IN ('manual','analysis','metadata')),
        evidence TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1)
      );
      CREATE INDEX IF NOT EXISTS media_tags_lookup ON media_tags(category, normalized_value, asset_id, segment_id);
      CREATE INDEX IF NOT EXISTS media_tags_owner ON media_tags(asset_id, segment_id, origin, category);
      CREATE VIEW IF NOT EXISTS effective_media_tags AS
        SELECT t.* FROM media_tags t WHERE t.origin='manual' OR NOT EXISTS (
          SELECT 1 FROM media_tags m WHERE m.asset_id=t.asset_id AND m.segment_id=t.segment_id
          AND m.category=t.category AND m.origin='manual'
        );
    `);
    if (this.db.prepare("SELECT 1 FROM sqlite_master WHERE name='asset_segments'").get()) {
      this.db.exec(`CREATE TRIGGER IF NOT EXISTS media_tags_delete_scene AFTER DELETE ON asset_segments
        BEGIN DELETE FROM media_tags WHERE segment_id=OLD.id AND asset_id=OLD.asset_id; END;`);
    }
    return !existing;
  }

  read(assetId: string, segmentId?: string): MediaTag[] {
    const rows = this.db.prepare(`SELECT * FROM effective_media_tags WHERE asset_id=?
      ${segmentId === undefined ? "" : "AND segment_id=?"} ORDER BY category, normalized_value, origin`)
      .all(...(segmentId === undefined ? [assetId] : [assetId, segmentId])) as Row[];
    return rows.map((row) => ({ id: String(row.id), segmentId: String(row.segment_id),
      category: row.category as MediaTag["category"], value: String(row.value),
      origin: row.origin as MediaTag["origin"], evidence: String(row.evidence), confidence: Number(row.confidence) }));
  }

  setManual(assetId: string, segmentId: string, values: string[]): void {
    if (values.length > 50) throw new Error("Use at most 50 manual tags per file or scene.");
    const tags = values.map((value) => parseMediaTag(value, true));
    this.db.prepare("DELETE FROM media_tags WHERE asset_id=? AND segment_id=? AND origin='manual'").run(assetId, segmentId);
    for (const tag of tags) this.insert(assetId, segmentId, tag, "manual", "user", 1);
    if (segmentId && tags.length) {
      // A manually tagged interval is durable just like a manually described one.
      this.db.prepare("UPDATE asset_segments SET locked=1 WHERE asset_id=? AND id=?").run(assetId, segmentId);
    }
  }

  refresh(asset: Row, scenes: Row[]): void {
    const assetId = String(asset.id);
    this.db.prepare("DELETE FROM media_tags WHERE asset_id=? AND origin<>'manual'").run(assetId);
    const add = (scope: string, category: TagFilter["category"], value: unknown, origin: MediaTag["origin"], evidence: string, confidence = 1) => {
      if (typeof value !== "string" || !value.trim()) return;
      this.insert(assetId, scope, { category, value: value.trim().slice(0, 160) }, origin, evidence, confidence);
    };
    const analysisTags = (scope: string, raw: unknown, evidence: string, confidence: number) => {
      for (const tag of selectSearchTags(tagStrings(raw))) {
        this.insert(assetId, scope, tag, "analysis", evidence, confidence);
      }
    };
    add("", "category", asset.media_kind, "metadata", "media_probe");
    add("", "format", path.extname(String(asset.canonical_path ?? "")).slice(1).toLowerCase(), "metadata", "file_extension");
    if (asset.width && asset.height) add("", "format", `${asset.width}x${asset.height}`, "metadata", "media_probe");
    add("", "format", asset.video_codec, "metadata", "media_probe");
    add("", "creator", asset.creator, "metadata", "source_metadata");
    try { add("", "source", new URL(String(asset.source_page_url || asset.source_url)).hostname, "metadata", "source_metadata"); } catch { /* Local asset. */ }
    const latestAnalysis = this.db.prepare("SELECT id FROM analysis_runs WHERE asset_id=? AND status='complete' AND requested_start_ms IS NULL ORDER BY completed_at DESC LIMIT 1").get(assetId) as Row | undefined;
    const assetEvidence = `analysis:${String(latestAnalysis?.id ?? "legacy")}`;
    analysisTags("", asset.tags_json, assetEvidence, .7);
    const fingerprints = new Map((this.db.prepare("SELECT id,input_fingerprint FROM analysis_runs WHERE asset_id=?").all(assetId) as Row[])
      .map((run) => [String(run.id), String(run.input_fingerprint)]));
    for (const scene of scenes) {
      const previousFingerprint = fingerprints.get(String(scene.analysis_run_id || ""));
      if (scene.origin !== "user" && (asset.analysis_state === "stale" || previousFingerprint !== undefined && previousFingerprint !== asset.quick_fingerprint)) continue;
      const scope = String(scene.id);
      const manual = scene.origin === "user";
      const origin = manual ? "metadata" : "analysis";
      const evidence = manual ? "user_description" : `analysis:${String(scene.analysis_run_id || "legacy")}`;
      const confidence = manual ? 1 : Math.max(0, Math.min(1, Number(scene.confidence) || 0));
      add(scope, "category", scene.visual_category, origin, evidence, confidence);
      analysisTags(scope, scene.tags_json, evidence, confidence);
    }
  }

  private insert(assetId: string, segmentId: string, tag: TagFilter, origin: MediaTag["origin"], evidence: string, confidence: number) {
    const normalized = normalizeTagValue(tag.value);
    if (!normalized) return;
    const duplicate = this.db.prepare("SELECT 1 FROM media_tags WHERE asset_id=? AND segment_id=? AND category=? AND normalized_value=? AND origin=?")
      .get(assetId, segmentId, tag.category, normalized, origin);
    if (duplicate) return;
    const id = crypto.createHash("sha256").update(JSON.stringify([assetId, segmentId, tag.category, normalized, origin, evidence])).digest("hex");
    this.db.prepare(`INSERT OR REPLACE INTO media_tags
      (id,asset_id,segment_id,category,value,normalized_value,origin,evidence,confidence) VALUES (?,?,?,?,?,?,?,?,?)`)
      .run(id, assetId, segmentId, tag.category, tag.value, normalized, origin, evidence, confidence);
  }
}

function tagStrings(raw: unknown): string[] {
  try {
    const values: unknown = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(values) ? values.filter((value): value is string => typeof value === "string").slice(0, 50) : [];
  } catch { return []; }
}
