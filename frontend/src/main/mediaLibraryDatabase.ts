import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import type {
  MediaAssetAvailability,
  MediaAssetDetail,
  MediaAssetKind,
  MediaAssetListRequest,
  MediaAssetListResult,
  MediaAssetAnalysisPayload,
  MediaAnalysisDetail,
  MediaAssetSegment,
  MediaAssetSummary,
  MediaAnalysisScope,
  MediaLibraryRoot,
  MediaLibraryRootKind,
  MediaLibraryRootPurpose,
  MediaTransparency,
} from "../renderer/lib/types";

const SCHEMA_VERSION = 6;
const MAX_PAGE_SIZE = 200;
const VISIBLE_ASSET_CONDITION = `NOT EXISTS (
  SELECT 1 FROM library_directory_visibility v
  WHERE v.root_id=a.root_id AND v.visible=0 AND (
    (v.kind='direct' AND v.relative_directory=a.relative_directory)
    OR (
      v.kind='subtree' AND (
        v.relative_directory=''
        OR a.relative_directory=v.relative_directory
        OR a.relative_directory LIKE v.relative_directory || '\\%'
      )
    )
  )
)`;

export type IndexedMediaFile = {
  canonicalPath: string;
  relativePath: string;
  mediaKind: MediaAssetKind;
  sizeBytes: number;
  mtimeNs: string;
  quickFingerprint: string;
  durationMs: number | null;
  width: number | null;
  height: number | null;
  frameRateNum: number | null;
  frameRateDen: number | null;
  videoCodec: string;
  audioCodec: string;
  hasAudio: boolean;
  transparency: MediaTransparency;
  availability: MediaAssetAvailability;
  error: string;
};

type SqlValue = string | number | bigint | null;
type Row = Record<string, unknown>;

export class MediaLibraryDatabase {
  private db: DatabaseSync;

  constructor(readonly databasePath: string) {
    fs.mkdirSync(path.dirname(databasePath), { recursive: true });
    this.db = this.open();
    const version = this.schemaVersion();
    if (version > SCHEMA_VERSION) {
      this.db.close();
      throw new Error(`Media library schema ${version} is newer than supported schema ${SCHEMA_VERSION}.`);
    }
    if (version > 0 && version < SCHEMA_VERSION) {
      this.db.exec("PRAGMA wal_checkpoint(TRUNCATE)");
      this.db.close();
      this.backupBeforeMigration(version);
      this.db = this.open();
    }
    this.migrate(version);
  }

  close(): void {
    if (this.db.isOpen) this.db.close();
  }

  listRoots(): MediaLibraryRoot[] {
    return this.db.prepare(`
      SELECT id, canonical_path, kind, purpose, recursive, enabled, created_at,
             last_scan_at, last_scan_status, last_error
      FROM library_roots
      ORDER BY kind DESC, canonical_path COLLATE NOCASE
    `).all().map((row) => mapRoot(row as Row));
  }

  addRoot(
    canonicalPath: string,
    kind: MediaLibraryRootKind,
    recursive = false,
    purpose: MediaLibraryRootPurpose = kind === "managed" ? "generated" : "user",
  ): MediaLibraryRoot {
    const normalized = path.resolve(canonicalPath);
    const now = new Date().toISOString();
    const existing = this.db.prepare(
      "SELECT id FROM library_roots WHERE canonical_path = ? COLLATE NOCASE",
    ).get(normalized) as Row | undefined;
    const id = stringValue(existing?.id) || crypto.randomUUID();
    this.db.prepare(`
      INSERT INTO library_roots (
        id, canonical_path, kind, purpose, recursive, enabled, created_at,
        last_scan_at, last_scan_status, last_error
      ) VALUES (?, ?, ?, ?, ?, 1, ?, '', 'never', '')
      ON CONFLICT(canonical_path) DO UPDATE SET
        kind=excluded.kind, purpose=excluded.purpose, recursive=0, enabled=1
    `).run(id, normalized, kind, purpose, recursive ? 1 : 0, now);
    return this.getRoot(id);
  }

  listDirectoryScopes(rootId: string): string[] {
    this.getRoot(rootId);
    return (this.db.prepare(`
      SELECT relative_directory FROM library_directory_scopes
      WHERE root_id=? AND included=1
      UNION ALL
      SELECT '' WHERE NOT EXISTS (
        SELECT 1 FROM library_directory_scopes
        WHERE root_id=? AND relative_directory=''
      )
      ORDER BY relative_directory COLLATE NOCASE
    `).all(rootId, rootId) as Row[]).map((row) => stringValue(row.relative_directory));
  }

  setDirectoryIncluded(rootId: string, relativeDirectory: string, included: boolean): void {
    this.getRoot(rootId);
    const normalized = normalizeRelativeDirectory(relativeDirectory);
    this.db.prepare(`
      INSERT INTO library_directory_scopes(root_id, relative_directory, included)
      VALUES (?, ?, ?)
      ON CONFLICT(root_id, relative_directory) DO UPDATE SET included=excluded.included
    `).run(rootId, normalized, included ? 1 : 0);
  }

  directoryTrackedCounts(rootId: string): Record<string, number> {
    this.getRoot(rootId);
    const rows = this.db.prepare(`
      SELECT relative_directory, COUNT(*) AS count FROM assets
      WHERE root_id=? GROUP BY relative_directory
    `).all(rootId) as Row[];
    return Object.fromEntries(rows.map((row) => [
      stringValue(row.relative_directory),
      numberValue(row.count),
    ]));
  }

  directoryVisibility(rootId: string): Array<{ relativeDirectory: string; kind: "subtree" | "direct"; visible: boolean }> {
    this.getRoot(rootId);
    return (this.db.prepare(`
      SELECT relative_directory, kind, visible
      FROM library_directory_visibility WHERE root_id=?
    `).all(rootId) as Row[]).map((row) => ({
      relativeDirectory: stringValue(row.relative_directory),
      kind: stringValue(row.kind) as "subtree" | "direct",
      visible: Boolean(numberValue(row.visible)),
    }));
  }

  setDirectoryVisible(
    rootId: string,
    relativeDirectory: string,
    kind: "subtree" | "direct",
    visible: boolean,
  ): void {
    this.getRoot(rootId);
    const normalized = normalizeRelativeDirectory(relativeDirectory);
    this.db.prepare(`
      INSERT INTO library_directory_visibility(root_id, relative_directory, kind, visible)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(root_id, relative_directory, kind) DO UPDATE SET visible=excluded.visible
    `).run(rootId, normalized, kind, visible ? 1 : 0);
  }

  directoryHiddenStates(rootId: string): Array<{ relativeDirectory: string; hidden: boolean }> {
    this.getRoot(rootId);
    return (this.db.prepare(`
      SELECT relative_directory, hidden
      FROM library_directory_ui_state WHERE root_id=?
    `).all(rootId) as Row[]).map((row) => ({
      relativeDirectory: stringValue(row.relative_directory),
      hidden: Boolean(numberValue(row.hidden)),
    }));
  }

  setDirectoryHidden(rootId: string, relativeDirectory: string, hidden: boolean): void {
    this.getRoot(rootId);
    const normalized = normalizeRelativeDirectory(relativeDirectory);
    this.db.prepare(`
      INSERT INTO library_directory_ui_state(root_id, relative_directory, hidden)
      VALUES (?, ?, ?)
      ON CONFLICT(root_id, relative_directory) DO UPDATE SET hidden=excluded.hidden
    `).run(rootId, normalized, hidden ? 1 : 0);
  }

  removeDirectoryAssets(rootId: string, relativeDirectory: string): { removedAssets: number; paths: string[] } {
    this.getRoot(rootId);
    const normalized = normalizeRelativeDirectory(relativeDirectory);
    const rows = this.db.prepare(`
      SELECT id, canonical_path FROM assets
      WHERE root_id=? AND relative_directory=?
    `).all(rootId, normalized) as Row[];
    const ids = rows.map((row) => stringValue(row.id));
    this.transaction(() => {
      for (const id of ids) this.deleteAssetRecords(id);
      this.db.prepare(`
        INSERT INTO library_directory_scopes(root_id, relative_directory, included)
        VALUES (?, ?, 0)
        ON CONFLICT(root_id, relative_directory) DO UPDATE SET included=0
      `).run(rootId, normalized);
    });
    return {
      removedAssets: ids.length,
      paths: rows.map((row) => stringValue(row.canonical_path)),
    };
  }

  setRootEnabled(rootId: string, enabled: boolean): MediaLibraryRoot {
    const result = this.db.prepare("UPDATE library_roots SET enabled=? WHERE id=?").run(enabled ? 1 : 0, rootId);
    if (Number(result.changes) !== 1) throw new Error("Media library root was not found.");
    return this.getRoot(rootId);
  }

  removeRoot(rootId: string): { removedAssets: number } {
    const root = this.getRoot(rootId);
    if (root.kind === "managed") throw new Error("The managed media location cannot be removed.");
    let removedAssets = 0;
    this.transaction(() => {
      this.db.prepare(`
        DELETE FROM embeddings
        WHERE (owner_type='asset' AND owner_id IN (SELECT id FROM assets WHERE root_id=?))
           OR (owner_type='segment' AND owner_id IN (
             SELECT s.id FROM asset_segments s JOIN assets a ON a.id=s.asset_id WHERE a.root_id=?
           ))
      `).run(rootId, rootId);
      this.db.prepare(`
        DELETE FROM jobs
        WHERE owner_id=? OR owner_id IN (SELECT id FROM assets WHERE root_id=?)
      `).run(rootId, rootId);
      this.db.prepare(`
        DELETE FROM asset_search
        WHERE asset_id IN (SELECT id FROM assets WHERE root_id=?)
      `).run(rootId);
      const result = this.db.prepare("DELETE FROM assets WHERE root_id=?").run(rootId);
      removedAssets = Number(result.changes);
      this.db.prepare("DELETE FROM library_roots WHERE id=?").run(rootId);
    });
    return { removedAssets };
  }

  beginScan(rootId: string): string {
    this.getRoot(rootId);
    const token = crypto.randomUUID();
    this.db.prepare(
      "UPDATE library_roots SET last_scan_status='running', last_error='' WHERE id=?",
    ).run(rootId);
    return token;
  }

  upsertIndexedFile(rootId: string, scanToken: string, file: IndexedMediaFile): string {
    const root = this.getRoot(rootId);
    const now = new Date().toISOString();
    const current = this.db.prepare(
      "SELECT id FROM assets WHERE canonical_path=? COLLATE NOCASE",
    ).get(file.canonicalPath) as Row | undefined;
    const id = stringValue(current?.id) || crypto.randomUUID();
    const title = path.basename(file.canonicalPath, path.extname(file.canonicalPath));
    const inferred = inferDescription(file.relativePath, file.mediaKind);
    this.db.prepare(`
      INSERT INTO assets (
        id, root_id, relative_path, relative_directory, canonical_path, media_kind, source_kind,
        availability, size_bytes, mtime_ns, quick_fingerprint, duration_ms,
        width, height, frame_rate_num, frame_rate_den, video_codec, audio_codec,
        has_audio, transparency, title, inferred_description, ai_description, user_description,
        tags_json, analysis_state, scan_token, last_error, created_at, updated_at,
        last_seen_at, source_url, source_page_url, creator, license_text, acquired_at
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '',
        '[]', 'metadata_only', ?, ?, ?, ?, ?, '', '', '', '', ''
      )
      ON CONFLICT(canonical_path) DO UPDATE SET
        root_id=excluded.root_id,
        relative_path=excluded.relative_path,
        relative_directory=excluded.relative_directory,
        media_kind=excluded.media_kind,
        source_kind=excluded.source_kind,
        availability=excluded.availability,
        size_bytes=excluded.size_bytes,
        mtime_ns=excluded.mtime_ns,
        quick_fingerprint=excluded.quick_fingerprint,
        duration_ms=excluded.duration_ms,
        width=excluded.width,
        height=excluded.height,
        frame_rate_num=excluded.frame_rate_num,
        frame_rate_den=excluded.frame_rate_den,
        video_codec=excluded.video_codec,
        audio_codec=excluded.audio_codec,
        has_audio=excluded.has_audio,
        transparency=excluded.transparency,
        title=excluded.title,
        inferred_description=excluded.inferred_description,
        scan_token=excluded.scan_token,
        last_error=excluded.last_error,
        updated_at=excluded.updated_at,
        last_seen_at=excluded.last_seen_at
    `).run(
      id,
      rootId,
      file.relativePath,
      normalizeRelativeDirectory(path.dirname(file.relativePath)),
      file.canonicalPath,
      file.mediaKind,
      root.purpose === "web" ? "web" : "local",
      file.availability,
      file.sizeBytes,
      file.mtimeNs,
      file.quickFingerprint,
      file.durationMs,
      file.width,
      file.height,
      file.frameRateNum,
      file.frameRateDen,
      file.videoCodec,
      file.audioCodec,
      file.hasAudio ? 1 : 0,
      file.transparency,
      title,
      inferred,
      scanToken,
      file.error,
      now,
      now,
      now,
    );
    this.refreshSearch(id);
    return id;
  }

  finishScan(rootId: string, scanToken: string, scannedDirectories?: string[], error = ""): { missing: number } {
    const now = new Date().toISOString();
    let missing = 0;
    this.transaction(() => {
      if (!error) {
        const normalized = scannedDirectories
          ? [...new Set(scannedDirectories.map(normalizeRelativeDirectory))]
          : null;
        const directoryCondition = normalized
          ? `AND relative_directory IN (${normalized.map(() => "?").join(",")})`
          : "";
        const result = normalized?.length === 0 ? { changes: 0 } : this.db.prepare(`
          UPDATE assets
          SET availability='missing', updated_at=?
          WHERE root_id=? AND scan_token<>? AND availability<>'missing'
            ${directoryCondition}
        `).run(now, rootId, scanToken, ...(normalized ?? []));
        missing = Number(result.changes);
      }
      this.db.prepare(`
        UPDATE library_roots
        SET last_scan_at=?, last_scan_status=?, last_error=?
        WHERE id=?
      `).run(now, error ? "failed" : "complete", error, rootId);
    });
    return { missing };
  }

  listAssets(request: MediaAssetListRequest = {}): MediaAssetListResult {
    const limit = clampInteger(request.limit, 1, MAX_PAGE_SIZE, 50);
    const offset = clampInteger(request.offset, 0, Number.MAX_SAFE_INTEGER, 0);
    const conditions: string[] = [
      "r.enabled=1",
      VISIBLE_ASSET_CONDITION,
    ];
    const values: SqlValue[] = [];
    let join = "JOIN library_roots r ON r.id=a.root_id";
    let order = "a.updated_at DESC, a.canonical_path COLLATE NOCASE";
    const fts = ftsQuery(request.query || "");
    if (fts) {
      join += " JOIN asset_search s ON s.asset_id=a.id";
      conditions.push("asset_search MATCH ?");
      values.push(fts);
      order = "bm25(asset_search), a.updated_at DESC";
    }
    if (request.rootId) {
      conditions.push("a.root_id=?");
      values.push(request.rootId);
    }
    if (request.relativeDirectory !== undefined) {
      conditions.push("a.relative_directory=?");
      values.push(normalizeRelativeDirectory(request.relativeDirectory));
    }
    if (request.mediaKind) {
      conditions.push("a.media_kind=?");
      values.push(request.mediaKind);
    }
    if (request.availability) {
      conditions.push("a.availability=?");
      values.push(request.availability);
    }
    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    const totalRow = this.db.prepare(
      `SELECT COUNT(*) AS total FROM assets a ${join} ${where}`,
    ).get(...values) as Row;
    const rows = this.db.prepare(`
      SELECT a.* FROM assets a ${join} ${where}
      ORDER BY ${order}
      LIMIT ? OFFSET ?
    `).all(...values, limit, offset) as Row[];
    return { assets: rows.map(mapAsset), total: numberValue(totalRow.total) };
  }

  listAnalysisCandidates(mediaKind?: MediaAssetKind): MediaAssetSummary[] {
    const conditions = [
      "r.enabled=1",
      VISIBLE_ASSET_CONDITION,
      "a.availability='active'",
      "a.analysis_state<>'ready'",
      "a.analysis_state<>'analyzing'",
    ];
    const values: SqlValue[] = [];
    if (mediaKind) {
      conditions.push("a.media_kind=?");
      values.push(mediaKind);
    }
    const rows = this.db.prepare(`
      SELECT a.*
      FROM assets a
      JOIN library_roots r ON r.id=a.root_id
      WHERE ${conditions.join(" AND ")}
      ORDER BY a.updated_at DESC
      LIMIT 10000
    `).all(...values) as Row[];
    return rows.map(mapAsset);
  }

  getAsset(assetId: string): MediaAssetDetail {
    const row = this.db.prepare("SELECT * FROM assets WHERE id=?").get(assetId) as Row | undefined;
    if (!row) throw new Error("Media asset was not found.");
    const segments = this.db.prepare(`
      SELECT * FROM asset_segments WHERE asset_id=? ORDER BY start_ms, end_ms
    `).all(assetId).map((item) => mapSegment(item as Row));
    return {
      ...mapAsset(row),
      sourceUrl: stringValue(row.source_url),
      sourcePageUrl: stringValue(row.source_page_url),
      creator: stringValue(row.creator),
      licenseText: stringValue(row.license_text),
      acquiredAt: stringValue(row.acquired_at),
      segments,
    };
  }

  updateUserDescription(assetId: string, description: string): MediaAssetDetail {
    this.getAsset(assetId);
    const now = new Date().toISOString();
    this.transaction(() => {
      this.db.prepare(`
        UPDATE asset_descriptions
        SET active=0
        WHERE asset_id=? AND segment_id IS NULL AND origin='user' AND active=1
      `).run(assetId);
      if (description) {
        this.db.prepare(`
          INSERT INTO asset_descriptions (
            id, asset_id, segment_id, origin, text, tags_json, confidence,
            provider, model, prompt_version, active, locked, created_at, updated_at
          ) VALUES (?, ?, NULL, 'user', ?, '[]', 1.0, '', '', '', 1, 1, ?, ?)
        `).run(crypto.randomUUID(), assetId, description, now, now);
      }
      this.db.prepare(
        "UPDATE assets SET user_description=?, updated_at=? WHERE id=?",
      ).run(description, now, assetId);
      this.refreshSearch(assetId);
    });
    return this.getAsset(assetId);
  }

  addUserSegment(assetId: string, scope: MediaAnalysisScope, description: string): MediaAssetDetail {
    const asset = this.getAsset(assetId);
    validateSegmentScope(scope, asset.durationMs);
    const text = description.trim();
    if (!text) throw new Error("A segment description is required.");
    const conflicting = this.db.prepare(`
      SELECT 1 FROM asset_segments
      WHERE asset_id=? AND origin='user' AND start_ms < ? AND end_ms > ?
      LIMIT 1
    `).get(assetId, scope.endMs, scope.startMs);
    if (conflicting) throw new Error("This range overlaps an existing user-described segment.");
    this.transaction(() => {
      this.trimAiSegments(assetId, scope.startMs, scope.endMs);
      this.db.prepare(`
        INSERT INTO asset_segments (
          id, asset_id, start_ms, end_ms, segment_kind, description, tags_json,
          confidence, motion_level, visual_category, suitability, origin, locked, analysis_run_id
        ) VALUES (?, ?, ?, ?, 'semantic_range', ?, '[]', 1.0, NULL, 'other', '', 'user', 1, '')
      `).run(crypto.randomUUID(), assetId, scope.startMs, scope.endMs, text);
      this.refreshSearch(assetId);
    });
    return this.getAsset(assetId);
  }

  updateProvenance(
    assetId: string,
    sourceUrl: string,
    sourcePageUrl: string,
    creator: string,
    licenseText: string,
    acquiredAt: string,
  ): MediaAssetDetail {
    this.getAsset(assetId);
    this.db.prepare(`
      UPDATE assets
      SET source_url=?, source_page_url=?, creator=?, license_text=?, acquired_at=?, updated_at=?
      WHERE id=?
    `).run(sourceUrl, sourcePageUrl, creator, licenseText, acquiredAt, acquiredAt, assetId);
    return this.getAsset(assetId);
  }

  startAnalysis(
    assetId: string,
    runId: string,
    provider: string,
    model: string,
    estimatedCostUsd: number,
    detail: MediaAnalysisDetail = "simple",
    scope?: MediaAnalysisScope,
  ): void {
    const asset = this.getAsset(assetId);
    if (asset.availability !== "active") throw new Error("Only available media can be analyzed.");
    if (scope) validateSegmentScope(scope, asset.durationMs);
    const now = new Date().toISOString();
    this.transaction(() => {
      this.db.prepare(`
        INSERT INTO analysis_runs (
          id, asset_id, provider, models_json, prompt_version, input_fingerprint,
          status, estimated_cost_usd, actual_cost_usd, error, started_at, completed_at,
          requested_start_ms, requested_end_ms
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, 0, '', ?, '', ?, ?)
      `).run(
        runId,
        assetId,
        provider,
        JSON.stringify([model]),
        `media-analysis-v6-${detail}`,
        asset.quickFingerprint,
        estimatedCostUsd,
        now,
        scope?.startMs ?? null,
        scope?.endMs ?? null,
      );
      this.db.prepare(`
        UPDATE assets SET analysis_state='analyzing', active_analysis_run_id=?, last_error='', updated_at=?
        WHERE id=?
      `).run(runId, now, assetId);
    });
  }

  completeAnalysis(
    assetId: string,
    runId: string,
    result: MediaAssetAnalysisPayload,
    scope?: MediaAnalysisScope,
  ): MediaAssetDetail {
    const asset = this.getAsset(assetId);
    if (asset.analysisState !== "analyzing") throw new Error("Media analysis is not active.");
    const now = new Date().toISOString();
    this.transaction(() => {
      if (scope) this.trimAiSegments(assetId, scope.startMs, scope.endMs);
      else this.db.prepare("DELETE FROM asset_segments WHERE asset_id=? AND origin='ai'").run(assetId);
      const insertSegment = this.db.prepare(`
        INSERT INTO asset_segments (
          id, asset_id, start_ms, end_ms, segment_kind, description, tags_json,
          confidence, motion_level, visual_category, suitability, origin, locked, analysis_run_id
        ) VALUES (?, ?, ?, ?, 'semantic_range', ?, ?, ?, ?, ?, ?, 'ai', 0, ?)
      `);
      const userRanges = (this.db.prepare(`
        SELECT start_ms, end_ms FROM asset_segments
        WHERE asset_id=? AND origin='user' ORDER BY start_ms, end_ms
      `).all(assetId) as Row[]).map((row) => ({
        startMs: numberValue(row.start_ms),
        endMs: numberValue(row.end_ms),
      }));
      for (const segment of result.segments) {
        const boundedStart = Math.max(scope?.startMs ?? 0, segment.start_ms);
        const boundedEnd = Math.min(scope?.endMs ?? (asset.durationMs ?? segment.end_ms), segment.end_ms);
        for (const part of subtractRanges({ startMs: boundedStart, endMs: boundedEnd }, userRanges)) {
          insertSegment.run(
            crypto.randomUUID(),
            assetId,
            part.startMs,
            part.endMs,
            segment.description,
            JSON.stringify(segment.tags),
            segment.confidence,
            segment.motion_level,
            segment.visual_category,
            segment.suitability,
            runId,
          );
        }
      }
      if (!scope) {
        this.db.prepare(`
          UPDATE asset_descriptions SET active=0
          WHERE asset_id=? AND segment_id IS NULL AND origin='ai' AND active=1
        `).run(assetId);
        this.db.prepare(`
          INSERT INTO asset_descriptions (
            id, asset_id, segment_id, origin, text, tags_json, confidence,
            provider, model, prompt_version, active, locked, created_at, updated_at
          ) VALUES (?, ?, NULL, 'ai', ?, ?, 1.0, ?, ?, ?, 1, 0, ?, ?)
        `).run(
          crypto.randomUUID(), assetId, result.description, JSON.stringify(result.tags),
          result.provider, result.model, result.prompt_version, now, now,
        );
        this.db.prepare(`
          UPDATE assets
          SET ai_description=?, tags_json=?, analysis_state='ready',
              active_analysis_run_id=NULL, last_error='', updated_at=?
          WHERE id=?
        `).run(result.description, JSON.stringify(result.tags), now, assetId);
      } else {
        this.db.prepare(`
          UPDATE assets
          SET analysis_state=CASE WHEN ai_description<>'' THEN 'ready' ELSE 'metadata_only' END,
              active_analysis_run_id=NULL, last_error='', updated_at=?
          WHERE id=?
        `).run(now, assetId);
      }
      this.db.prepare(`
        UPDATE analysis_runs
        SET status='complete', actual_cost_usd=?, completed_at=?
        WHERE id=? AND asset_id=?
      `).run(result.cost_usd, now, runId, assetId);
      this.refreshSearch(assetId);
    });
    return this.getAsset(assetId);
  }

  failAnalysis(assetId: string, runId: string, error: string): MediaAssetDetail {
    const now = new Date().toISOString();
    this.transaction(() => {
      this.db.prepare(`
        UPDATE assets
        SET analysis_state='failed', active_analysis_run_id=NULL, last_error=?, updated_at=?
        WHERE id=?
      `).run(error, now, assetId);
      this.db.prepare(`
        UPDATE analysis_runs SET status='failed', error=?, completed_at=?
        WHERE id=? AND asset_id=?
      `).run(error, now, runId, assetId);
    });
    return this.getAsset(assetId);
  }

  private getRoot(rootId: string): MediaLibraryRoot {
    const row = this.db.prepare(`
      SELECT id, canonical_path, kind, purpose, recursive, enabled, created_at,
             last_scan_at, last_scan_status, last_error
      FROM library_roots WHERE id=?
    `).get(rootId) as Row | undefined;
    if (!row) throw new Error("Media library root was not found.");
    return mapRoot(row);
  }

  private refreshSearch(assetId: string): void {
    const row = this.db.prepare("SELECT * FROM assets WHERE id=?").get(assetId) as Row | undefined;
    if (!row) return;
    const segmentText = (this.db.prepare(`
      SELECT description FROM asset_segments WHERE asset_id=? ORDER BY start_ms, end_ms
    `).all(assetId) as Row[]).map((segment) => stringValue(segment.description)).join(" ");
    const effective = `${effectiveDescription(row)} ${segmentText}`.trim();
    this.db.prepare("DELETE FROM asset_search WHERE asset_id=?").run(assetId);
    this.db.prepare(`
      INSERT INTO asset_search(asset_id, title, description, tags, path)
      VALUES (?, ?, ?, ?, ?)
    `).run(
      assetId,
      stringValue(row.title),
      effective,
      parseTags(row.tags_json).join(" "),
      `${stringValue(row.relative_path)} ${stringValue(row.canonical_path)}`,
    );
  }

  private trimAiSegments(assetId: string, startMs: number, endMs: number): void {
    const rows = this.db.prepare(`
      SELECT * FROM asset_segments
      WHERE asset_id=? AND origin='ai' AND start_ms < ? AND end_ms > ?
    `).all(assetId, endMs, startMs) as Row[];
    const insert = this.db.prepare(`
      INSERT INTO asset_segments (
        id, asset_id, start_ms, end_ms, segment_kind, description, tags_json,
        confidence, motion_level, visual_category, suitability, origin, locked, analysis_run_id
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai', 0, ?)
    `);
    for (const row of rows) {
      this.db.prepare("DELETE FROM asset_segments WHERE id=?").run(stringValue(row.id));
      const pieces = [
        { startMs: numberValue(row.start_ms), endMs: Math.min(startMs, numberValue(row.end_ms)) },
        { startMs: Math.max(endMs, numberValue(row.start_ms)), endMs: numberValue(row.end_ms) },
      ].filter((part) => part.endMs > part.startMs);
      for (const part of pieces) {
        insert.run(
          crypto.randomUUID(), assetId, part.startMs, part.endMs,
          stringValue(row.segment_kind), stringValue(row.description), stringValue(row.tags_json),
          numberValue(row.confidence), nullableNumber(row.motion_level), stringValue(row.visual_category),
          stringValue(row.suitability), stringValue(row.analysis_run_id),
        );
      }
    }
  }

  private deleteAssetRecords(assetId: string): void {
    this.db.prepare(`
      DELETE FROM embeddings
      WHERE (owner_type='asset' AND owner_id=?)
         OR (owner_type='segment' AND owner_id IN (
           SELECT id FROM asset_segments WHERE asset_id=?
         ))
    `).run(assetId, assetId);
    this.db.prepare("DELETE FROM jobs WHERE owner_id=?").run(assetId);
    this.db.prepare("DELETE FROM asset_search WHERE asset_id=?").run(assetId);
    this.db.prepare("DELETE FROM assets WHERE id=?").run(assetId);
  }

  private schemaVersion(): number {
    const row = this.db.prepare("PRAGMA user_version").get() as Row;
    return numberValue(row.user_version);
  }

  private open(): DatabaseSync {
    const db = new DatabaseSync(this.databasePath, { timeout: 5_000 });
    db.exec("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;");
    return db;
  }

  private migrate(version: number): void {
    if (version < 1) {
      this.transaction(() => {
      this.db.exec(`
        CREATE TABLE library_roots (
          id TEXT PRIMARY KEY,
          canonical_path TEXT NOT NULL UNIQUE COLLATE NOCASE,
          kind TEXT NOT NULL CHECK(kind IN ('referenced', 'managed')),
          purpose TEXT NOT NULL DEFAULT 'user' CHECK(purpose IN ('user', 'generated', 'web')),
          recursive INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          last_scan_at TEXT NOT NULL DEFAULT '',
          last_scan_status TEXT NOT NULL DEFAULT 'never'
            CHECK(last_scan_status IN ('never', 'running', 'complete', 'failed')),
          last_error TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE assets (
          id TEXT PRIMARY KEY,
          root_id TEXT NOT NULL REFERENCES library_roots(id),
          relative_path TEXT NOT NULL,
          relative_directory TEXT NOT NULL DEFAULT '',
          canonical_path TEXT NOT NULL UNIQUE COLLATE NOCASE,
          media_kind TEXT NOT NULL CHECK(media_kind IN ('video', 'image')),
          source_kind TEXT NOT NULL CHECK(source_kind IN ('local', 'web')),
          availability TEXT NOT NULL CHECK(availability IN ('active', 'missing', 'incompatible')),
          size_bytes INTEGER NOT NULL,
          mtime_ns TEXT NOT NULL,
          quick_fingerprint TEXT NOT NULL,
          sha256 TEXT NOT NULL DEFAULT '',
          duration_ms INTEGER,
          width INTEGER,
          height INTEGER,
          frame_rate_num INTEGER,
          frame_rate_den INTEGER,
          video_codec TEXT NOT NULL DEFAULT '',
          audio_codec TEXT NOT NULL DEFAULT '',
          has_audio INTEGER NOT NULL DEFAULT 0,
          transparency TEXT NOT NULL DEFAULT 'unknown'
            CHECK(transparency IN ('present', 'absent', 'unsupported', 'unknown')),
          title TEXT NOT NULL,
          inferred_description TEXT NOT NULL DEFAULT '',
          ai_description TEXT NOT NULL DEFAULT '',
          user_description TEXT NOT NULL DEFAULT '',
          tags_json TEXT NOT NULL DEFAULT '[]',
          analysis_state TEXT NOT NULL DEFAULT 'metadata_only'
            CHECK(analysis_state IN ('metadata_only', 'queued', 'analyzing', 'ready', 'failed', 'stale')),
          active_analysis_run_id TEXT,
          scan_token TEXT NOT NULL DEFAULT '',
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          source_url TEXT NOT NULL DEFAULT '',
          source_page_url TEXT NOT NULL DEFAULT '',
          creator TEXT NOT NULL DEFAULT '',
          license_text TEXT NOT NULL DEFAULT '',
          acquired_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX assets_root ON assets(root_id);
        CREATE INDEX assets_root_directory ON assets(root_id, relative_directory);
        CREATE INDEX assets_availability ON assets(availability);
        CREATE INDEX assets_fingerprint ON assets(size_bytes, mtime_ns, quick_fingerprint);

        CREATE TABLE asset_segments (
          id TEXT PRIMARY KEY,
          asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          start_ms INTEGER NOT NULL,
          end_ms INTEGER NOT NULL,
          segment_kind TEXT NOT NULL CHECK(segment_kind IN ('shot', 'semantic_range')),
          description TEXT NOT NULL DEFAULT '',
          tags_json TEXT NOT NULL DEFAULT '[]',
          confidence REAL NOT NULL DEFAULT 0,
          motion_level REAL,
          visual_category TEXT NOT NULL DEFAULT '',
          suitability TEXT NOT NULL DEFAULT '',
          origin TEXT NOT NULL DEFAULT 'ai' CHECK(origin IN ('ai', 'user')),
          locked INTEGER NOT NULL DEFAULT 0,
          analysis_run_id TEXT NOT NULL DEFAULT '',
          CHECK(start_ms >= 0 AND end_ms > start_ms)
        );
        CREATE INDEX asset_segments_asset_time ON asset_segments(asset_id, start_ms, end_ms);

        CREATE TABLE asset_descriptions (
          id TEXT PRIMARY KEY,
          asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          segment_id TEXT REFERENCES asset_segments(id) ON DELETE CASCADE,
          origin TEXT NOT NULL CHECK(origin IN ('filename', 'web_metadata', 'ai', 'user')),
          text TEXT NOT NULL,
          tags_json TEXT NOT NULL DEFAULT '[]',
          confidence REAL NOT NULL DEFAULT 0,
          provider TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          prompt_version TEXT NOT NULL DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          locked INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX asset_descriptions_owner ON asset_descriptions(asset_id, segment_id, active);

        CREATE TABLE embeddings (
          owner_type TEXT NOT NULL CHECK(owner_type IN ('asset', 'segment')),
          owner_id TEXT NOT NULL,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          vector BLOB NOT NULL,
          input_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(owner_type, owner_id, provider, model)
        );

        CREATE TABLE analysis_runs (
          id TEXT PRIMARY KEY,
          asset_id TEXT REFERENCES assets(id) ON DELETE CASCADE,
          provider TEXT NOT NULL,
          models_json TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          input_fingerprint TEXT NOT NULL,
          status TEXT NOT NULL,
          estimated_cost_usd REAL NOT NULL DEFAULT 0,
          actual_cost_usd REAL NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL DEFAULT '',
          requested_start_ms INTEGER,
          requested_end_ms INTEGER
        );

        CREATE TABLE jobs (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          owner_id TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'complete', 'failed', 'cancelled')),
          progress REAL NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE web_candidates (
          id TEXT PRIMARY KEY,
          discovery_run_id TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_page_url TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL DEFAULT '',
          creator TEXT NOT NULL DEFAULT '',
          license_text TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          proposed_description TEXT NOT NULL DEFAULT '',
          final_description TEXT NOT NULL DEFAULT '',
          requested_start_ms INTEGER,
          requested_end_ms INTEGER,
          staging_path TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL CHECK(state IN ('discovered', 'staged', 'approved', 'rejected', 'expired')),
          expires_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE library_directory_scopes (
          root_id TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
          relative_directory TEXT NOT NULL,
          included INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(root_id, relative_directory)
        );

        CREATE TABLE library_directory_visibility (
          root_id TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
          relative_directory TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('subtree', 'direct')),
          visible INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(root_id, relative_directory, kind)
        );

        CREATE TABLE library_directory_ui_state (
          root_id TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
          relative_directory TEXT NOT NULL,
          hidden INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(root_id, relative_directory)
        );

        CREATE VIRTUAL TABLE asset_search USING fts5(
          asset_id UNINDEXED,
          title,
          description,
          tags,
          path,
          tokenize='unicode61'
        );
      `);
      this.db.exec(`PRAGMA user_version=${SCHEMA_VERSION}`);
      });
      return;
    }
    if (version < 2) {
      this.transaction(() => {
        this.db.exec(`
          ALTER TABLE library_roots ADD COLUMN purpose TEXT NOT NULL DEFAULT 'user'
            CHECK(purpose IN ('user', 'generated', 'web'));
          ALTER TABLE assets ADD COLUMN relative_directory TEXT NOT NULL DEFAULT '';
          CREATE INDEX assets_root_directory ON assets(root_id, relative_directory);
          CREATE TABLE library_directory_scopes (
            root_id TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
            relative_directory TEXT NOT NULL,
            PRIMARY KEY(root_id, relative_directory)
          );
          UPDATE library_roots
          SET purpose=CASE WHEN kind='managed' THEN 'generated' ELSE 'user' END,
              recursive=0;
        `);
        const rows = this.db.prepare("SELECT id, relative_path FROM assets").all() as Row[];
        const update = this.db.prepare("UPDATE assets SET relative_directory=? WHERE id=?");
        for (const row of rows) {
          update.run(
            normalizeRelativeDirectory(path.dirname(stringValue(row.relative_path))),
            stringValue(row.id),
          );
        }
        this.db.exec("PRAGMA user_version=2");
      });
    }
    if (version < 3) {
      this.transaction(() => {
        this.db.exec(`
          ALTER TABLE library_directory_scopes
          ADD COLUMN included INTEGER NOT NULL DEFAULT 1;
          PRAGMA user_version=3;
        `);
      });
    }
    if (version < 4) {
      this.transaction(() => {
        this.db.exec(`
          CREATE TABLE library_directory_visibility (
            root_id TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
            relative_directory TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('subtree', 'direct')),
            visible INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(root_id, relative_directory, kind)
          );
          PRAGMA user_version=4;
        `);
      });
    }
    if (version < 5) {
      this.transaction(() => {
        this.db.exec(`
          ALTER TABLE assets
          ADD COLUMN transparency TEXT NOT NULL DEFAULT 'unknown'
            CHECK(transparency IN ('present', 'absent', 'unsupported', 'unknown'));
          CREATE TABLE library_directory_ui_state (
            root_id TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
            relative_directory TEXT NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(root_id, relative_directory)
          );
          PRAGMA user_version=5;
        `);
      });
    }
    if (version < 6) {
      this.transaction(() => {
        const tables = new Set(
          (this.db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as Row[])
            .map((row) => stringValue(row.name)),
        );
        if (tables.has("asset_segments")) {
          this.db.exec(`
            ALTER TABLE asset_segments ADD COLUMN origin TEXT NOT NULL DEFAULT 'ai'
              CHECK(origin IN ('ai', 'user'));
            ALTER TABLE asset_segments ADD COLUMN locked INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE asset_segments ADD COLUMN analysis_run_id TEXT NOT NULL DEFAULT '';
          `);
        }
        if (tables.has("analysis_runs")) {
          this.db.exec(`
            ALTER TABLE analysis_runs ADD COLUMN requested_start_ms INTEGER;
            ALTER TABLE analysis_runs ADD COLUMN requested_end_ms INTEGER;
          `);
        }
        this.db.exec("PRAGMA user_version=6");
      });
    }
  }

  private backupBeforeMigration(version: number): void {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backup = `${this.databasePath}.schema-${version}.${stamp}.bak`;
    fs.copyFileSync(this.databasePath, backup);
    const backups = fs.readdirSync(path.dirname(this.databasePath))
      .filter((name) => name.startsWith(`${path.basename(this.databasePath)}.schema-`) && name.endsWith(".bak"))
      .sort()
      .reverse();
    for (const old of backups.slice(3)) fs.rmSync(path.join(path.dirname(this.databasePath), old));
  }

  private transaction(action: () => void): void {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      action();
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
}

function mapRoot(row: Row): MediaLibraryRoot {
  return {
    id: stringValue(row.id),
    canonicalPath: stringValue(row.canonical_path),
    kind: stringValue(row.kind) as MediaLibraryRootKind,
    purpose: stringValue(row.purpose || (row.kind === "managed" ? "generated" : "user")) as MediaLibraryRootPurpose,
    recursive: Boolean(numberValue(row.recursive)),
    enabled: Boolean(numberValue(row.enabled)),
    createdAt: stringValue(row.created_at),
    lastScanAt: stringValue(row.last_scan_at),
    lastScanStatus: stringValue(row.last_scan_status) as MediaLibraryRoot["lastScanStatus"],
    lastError: stringValue(row.last_error),
  };
}

function mapAsset(row: Row): MediaAssetSummary {
  return {
    id: stringValue(row.id),
    rootId: stringValue(row.root_id),
    canonicalPath: stringValue(row.canonical_path),
    relativePath: stringValue(row.relative_path),
    relativeDirectory: stringValue(row.relative_directory),
    mediaKind: stringValue(row.media_kind) as MediaAssetKind,
    sourceKind: stringValue(row.source_kind) as MediaAssetSummary["sourceKind"],
    availability: stringValue(row.availability) as MediaAssetAvailability,
    analysisState: stringValue(row.analysis_state) as MediaAssetSummary["analysisState"],
    title: stringValue(row.title),
    effectiveDescription: effectiveDescription(row),
    userDescription: stringValue(row.user_description),
    aiDescription: stringValue(row.ai_description),
    inferredDescription: stringValue(row.inferred_description),
    tags: parseTags(row.tags_json),
    sizeBytes: numberValue(row.size_bytes),
    durationMs: nullableNumber(row.duration_ms),
    width: nullableNumber(row.width),
    height: nullableNumber(row.height),
    videoCodec: stringValue(row.video_codec),
    audioCodec: stringValue(row.audio_codec),
    hasAudio: Boolean(numberValue(row.has_audio)),
    transparency: (stringValue(row.transparency) || "unknown") as MediaTransparency,
    mtimeNs: stringValue(row.mtime_ns),
    quickFingerprint: stringValue(row.quick_fingerprint),
    lastSeenAt: stringValue(row.last_seen_at),
    updatedAt: stringValue(row.updated_at),
  };
}

function mapSegment(row: Row): MediaAssetSegment {
  return {
    id: stringValue(row.id),
    assetId: stringValue(row.asset_id),
    startMs: numberValue(row.start_ms),
    endMs: numberValue(row.end_ms),
    segmentKind: stringValue(row.segment_kind) as MediaAssetSegment["segmentKind"],
    description: stringValue(row.description),
    tags: parseTags(row.tags_json),
    confidence: numberValue(row.confidence),
    motionLevel: nullableNumber(row.motion_level),
    visualCategory: stringValue(row.visual_category),
    suitability: stringValue(row.suitability),
    origin: (stringValue(row.origin) || "ai") as MediaAssetSegment["origin"],
    locked: Boolean(numberValue(row.locked)),
  };
}

function validateSegmentScope(scope: MediaAnalysisScope, durationMs: number | null): void {
  if (
    !Number.isSafeInteger(scope.startMs)
    || !Number.isSafeInteger(scope.endMs)
    || scope.startMs < 0
    || scope.endMs <= scope.startMs
    || (durationMs !== null && scope.endMs > durationMs)
  ) {
    throw new Error("Segment range must be inside the video and have a positive duration.");
  }
}

function subtractRanges(
  source: MediaAnalysisScope,
  exclusions: MediaAnalysisScope[],
): MediaAnalysisScope[] {
  if (source.endMs <= source.startMs) return [];
  let parts = [source];
  for (const exclusion of exclusions) {
    parts = parts.flatMap((part) => {
      if (exclusion.endMs <= part.startMs || exclusion.startMs >= part.endMs) return [part];
      const next: MediaAnalysisScope[] = [];
      if (exclusion.startMs > part.startMs) next.push({ startMs: part.startMs, endMs: exclusion.startMs });
      if (exclusion.endMs < part.endMs) next.push({ startMs: exclusion.endMs, endMs: part.endMs });
      return next;
    });
  }
  return parts;
}

function effectiveDescription(row: Row): string {
  return stringValue(row.user_description)
    || stringValue(row.ai_description)
    || stringValue(row.inferred_description);
}

function inferDescription(relativePath: string, kind: MediaAssetKind): string {
  const withoutExtension = relativePath.slice(0, -path.extname(relativePath).length);
  const words = withoutExtension.replace(/[\\/_.-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? `${kind === "video" ? "Video" : "Image"}: ${words}` : kind;
}

function parseTags(value: unknown): string[] {
  try {
    const parsed: unknown = JSON.parse(stringValue(value) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function ftsQuery(query: string): string {
  const tokens = query.trim().split(/\s+/u).filter(Boolean).slice(0, 20);
  return tokens.map((token) => `"${token.replaceAll("\"", "\"\"")}"*`).join(" AND ");
}

function normalizeRelativeDirectory(value: string): string {
  const normalized = path.normalize(value.trim());
  if (!normalized || normalized === ".") return "";
  if (path.isAbsolute(normalized) || normalized === ".." || normalized.startsWith(`..${path.sep}`)) {
    throw new Error("Directory scope must stay inside its media location.");
  }
  return normalized;
}

function clampInteger(value: number | undefined, min: number, max: number, fallback: number): number {
  return Number.isSafeInteger(value) ? Math.min(max, Math.max(min, Number(value))) : fallback;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : Number(value) || 0;
}

function nullableNumber(value: unknown): number | null {
  return value === null || value === undefined ? null : numberValue(value);
}
