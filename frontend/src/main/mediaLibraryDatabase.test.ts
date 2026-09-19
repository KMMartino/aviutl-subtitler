import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it } from "vitest";
import { MediaLibraryDatabase, type IndexedMediaFile } from "./mediaLibraryDatabase";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("media library database", () => {
  it("indexes, searches, describes, and marks referenced assets missing", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "state", "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      const firstScan = database.beginScan(mediaRoot.id);
      const assetId = database.upsertIndexedFile(
        mediaRoot.id,
        firstScan,
        mediaFile(path.join(root, "media", "elden-ring", "boss-fight.mp4")),
      );
      expect(database.finishScan(mediaRoot.id, firstScan)).toEqual({ missing: 0 });

      const indexed = database.listAssets({ query: "boss fight" });
      expect(indexed.total).toBe(1);
      expect(indexed.assets[0]).toMatchObject({
        id: assetId,
        mediaKind: "video",
        availability: "active",
        effectiveDescription: "Video: elden ring boss fight",
      });

      const described = database.updateUserDescription(assetId, "Elden Ring boss fight in a ruined arena");
      expect(described.userDescription).toBe("Elden Ring boss fight in a ruined arena");
      expect(database.listAssets({ query: "ruined arena" }).assets[0].id).toBe(assetId);

      const emptyScan = database.beginScan(mediaRoot.id);
      expect(database.finishScan(mediaRoot.id, emptyScan)).toEqual({ missing: 1 });
      expect(database.getAsset(assetId).availability).toBe("missing");
    } finally {
      database.close();
    }
  });

  it("filters analysis status before pagination and includes unfinished states as not analyzed", () => {
    const root = temporaryRoot();
    const file = path.join(root, "library.sqlite3");
    const database = new MediaLibraryDatabase(file);
    try {
      const location = database.addRoot(path.join(root, "media"), "referenced");
      const scan = database.beginScan(location.id);
      const raw = new DatabaseSync(file);
      try {
        for (const state of ["ready", "metadata_only", "queued", "analyzing", "failed", "stale"]) {
          const id = database.upsertIndexedFile(location.id, scan, mediaFile(path.join(root, "media", `${state}.mp4`)));
          raw.prepare("UPDATE assets SET analysis_state=? WHERE id=?").run(state, id);
        }
      } finally { raw.close(); }
      expect(database.listAssets({ analysisStatus: "analyzed" }).assets.map((asset) => asset.analysisState)).toEqual(["ready"]);
      const unfinished = database.listAssets({ analysisStatus: "unanalyzed", limit: 2, offset: 2 });
      expect(unfinished.total).toBe(5);
      expect(unfinished.assets).toHaveLength(2);
      expect(unfinished.assets.every((asset) => asset.analysisState !== "ready")).toBe(true);
      expect(database.listAssets().total).toBe(6);
    } finally { database.close(); }
  });

  it("keeps FTS queries parameterized and bounded", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      const token = database.beginScan(mediaRoot.id);
      database.upsertIndexedFile(mediaRoot.id, token, mediaFile(path.join(root, "media", "trailer.mp4")));
      database.finishScan(mediaRoot.id, token);
      expect(database.listAssets({ query: "\" OR 1=1 --", limit: 500 }).assets).toEqual([]);
      expect(database.listAssets({ limit: 500 }).assets).toHaveLength(1);
    } finally {
      database.close();
    }
  });

  it("uses enabled locations as the catalog filter and removes a whole referenced location", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      expect(mediaRoot.recursive).toBe(false);
      const token = database.beginScan(mediaRoot.id);
      database.upsertIndexedFile(mediaRoot.id, token, mediaFile(path.join(root, "media", "clip.mp4")));
      database.finishScan(mediaRoot.id, token);
      database.setRootEnabled(mediaRoot.id, false);
      expect(database.listAssets().total).toBe(0);
      database.setRootEnabled(mediaRoot.id, true);
      expect(database.removeRoot(mediaRoot.id)).toEqual({ removedAssets: 1 });
      expect(database.listRoots()).toEqual([]);
      expect(database.listAssets().total).toBe(0);
    } finally {
      database.close();
    }
  });

  it("automatically tags explicit metadata and scenes without expanding description words while preserving manual corrections", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const location = database.addRoot(path.join(root, "media"), "referenced");
      const scan = database.beginScan(location.id);
      const file = mediaFile(path.join(root, "media", "arena.mp4"));
      const id = database.upsertIndexedFile(location.id, scan, file);
      expect(database.getAsset(id).structuredTags).toEqual(expect.arrayContaining([
        expect.objectContaining({ category: "format", value: "mp4", origin: "metadata", evidence: "file_extension" }),
      ]));
      database.updateUserDescription(id, "Castle exploration");
      database.updateProvenance(id, "https://example.org/video", "https://example.org/press", "Studio A", "", "now");
      expect(database.listAssets({ query: 'creator:"Studio A" source:example.org castle' }).total).toBe(1);
      database.updateUserDescription(id, "Forest exploration");
      expect(database.listAssets({ query: "keyword:castle" }).total).toBe(0);
      const analysis = {
        description: "Combat", tags: ["game:Wrong Game", "subject:knight"],
        segments: [{ start_ms: 0, end_ms: 30_000, description: "Knight evades", observed_label: "Dodging",
          tags: ["action:dodging", "tone:tense"], confidence: .9, motion_level: .5, visual_category: "gameplay", suitability: "Explanation" }],
        provider: "openai", model: "test", prompt_version: "test", sample_count: 30, input_tokens: 1, output_tokens: 1, cost_usd: .01,
      };
      database.startAnalysis(id, "one", "openai", "test", .01);
      const analyzed = database.completeAnalysis(id, "one", analysis);
      expect(analyzed.segments[0].structuredTags).toEqual(expect.arrayContaining([
        expect.objectContaining({ category: "action", value: "dodging", origin: "analysis", evidence: "analysis:one", confidence: .9 }),
      ]));
      database.updateTags(id, "", ["game:Right Game"]);
      const sceneId = analyzed.segments[0].id;
      database.updateTags(id, sceneId, ["action:parrying"]);
      expect(database.listAssets({ query: 'game:"Right Game" action:parrying' }).total).toBe(1);
      expect(database.listAssets({ query: 'game:"Wrong Game"' }).total).toBe(0);
      expect(database.listAssets({ query: "action:dodging" }).total).toBe(0);
      database.startAnalysis(id, "two", "openai", "test", .01);
      database.completeAnalysis(id, "two", analysis);
      database.upsertIndexedFile(location.id, scan, file);
      expect(database.getAsset(id).segments[0].id).toBe(sceneId);
      expect(database.listAssets({ query: 'game:"Right Game" action:parrying' }).total).toBe(1);
      expect(() => database.updateTags(id, "someone-elses-scene", ["action:jumping"])).toThrow(/belong/);
      expect(() => database.updateTags(id, "", ["game:New", "invalid:value"])).toThrow();
      expect(database.listAssets({ query: 'game:"Right Game"' }).total).toBe(1);
      database.upsertIndexedFile(location.id, scan, { ...file, quickFingerprint: "changed-media" });
      expect(database.getAsset(id).analysisState).toBe("stale");
      expect(database.listAssets({ query: "subject:knight" }).total).toBe(0);
      expect(database.listAssets({ query: "tone:tense" }).total).toBe(0);
      expect(database.listAssets({ query: 'game:"Right Game" action:parrying' }).total).toBe(1);
    } finally { database.close(); }
  });

  it("backfills collected information when upgrading a library without the tag index", () => {
    const root = temporaryRoot();
    const file = path.join(root, "library.sqlite3");
    const original = new MediaLibraryDatabase(file);
    const location = original.addRoot(path.join(root, "media"), "referenced");
    const scan = original.beginScan(location.id);
    const id = original.upsertIndexedFile(location.id, scan, mediaFile(path.join(root, "media", "recording.mp4")));
    original.updateUserDescription(id, "Old cathedral recording");
    original.close();
    const legacy = new DatabaseSync(file);
    legacy.exec("DROP TRIGGER media_tags_delete_scene; DROP VIEW effective_media_tags; DROP TABLE media_tags; PRAGMA user_version=7;");
    legacy.close();
    const upgraded = new MediaLibraryDatabase(file);
    try {
      expect(upgraded.listAssets({ query: "cathedral format:mp4" }).assets[0].id).toBe(id);
      expect(upgraded.getAsset(id).userDescription).toBe("Old cathedral recording");
    } finally { upgraded.close(); }
  });

  it("cleans old automatic tags on upgrade while preserving manual tags and descriptions", () => {
    const root = temporaryRoot();
    const file = path.join(root, "library.sqlite3");
    const original = new MediaLibraryDatabase(file);
    const location = original.addRoot(path.join(root, "media"), "referenced");
    const id = original.upsertIndexedFile(location.id, original.beginScan(location.id), mediaFile(path.join(root, "media", "trailer.mp4")));
    original.updateTags(id, "", ["subject:interior"]);
    original.updateUserDescription(id, "Preserved description");
    original.close();
    const legacy = new DatabaseSync(file);
    legacy.prepare("UPDATE assets SET tags_json=? WHERE id=?").run(JSON.stringify(["colorful", "interior", "game", "announcement", "game announcement", "platform:PlayStation"]), id);
    legacy.prepare("INSERT INTO media_tags VALUES ('old',?,'','keyword','colorful','colorful','analysis','legacy',0.7)").run(id);
    legacy.exec("PRAGMA user_version=8");
    legacy.close();
    const upgraded = new MediaLibraryDatabase(file);
    try {
      expect(upgraded.listAssets({ query: "keyword:colorful" }).total).toBe(0);
      expect(upgraded.listAssets({ query: 'keyword:"game announcement" platform:PlayStation' }).total).toBe(1);
      expect(upgraded.getAsset(id).structuredTags).toContainEqual(expect.objectContaining({ category: "subject", value: "interior", origin: "manual" }));
      expect(upgraded.getAsset(id).userDescription).toBe("Preserved description");
      expect(upgraded.getAsset(id).structuredTags.some((tag) => tag.value === "Preserved")).toBe(false);
    } finally { upgraded.close(); }
  });

  it("migrates scene labels without losing existing user ranges", () => {
    const root = temporaryRoot();
    const file = path.join(root, "library.sqlite3");
    const original = new MediaLibraryDatabase(file);
    const mediaRoot = original.addRoot(path.join(root, "media"), "referenced");
    const scan = original.beginScan(mediaRoot.id);
    const id = original.upsertIndexedFile(mediaRoot.id, scan, mediaFile(path.join(root, "media", "clip.mp4")));
    original.addUserSegment(id, { startMs: 1000, endMs: 2000 }, "My dodge scene");
    original.close();
    const legacy = new DatabaseSync(file);
    legacy.exec(`ALTER TABLE asset_segments DROP COLUMN observed_label;
      ALTER TABLE asset_segments DROP COLUMN evidence_spacing_sec;
      ALTER TABLE asset_segments DROP COLUMN handoff_reason;
      PRAGMA user_version=6;`);
    legacy.close();
    const migrated = new MediaLibraryDatabase(file);
    try {
      expect(migrated.getAsset(id).segments[0]).toMatchObject({
        description: "My dodge scene", origin: "user", locked: true, observedLabel: "", evidenceSpacingSec: 0,
      });
      expect(migrated.listAssets({ query: "dodge" }).assets[0].id).toBe(id);
    } finally { migrated.close(); }
  });

  it("versions AI analysis while preserving a user override", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      const token = database.beginScan(mediaRoot.id);
      const assetId = database.upsertIndexedFile(mediaRoot.id, token, mediaFile(path.join(root, "media", "gameplay.mp4")));
      database.finishScan(mediaRoot.id, token);
      database.updateUserDescription(assetId, "My authoritative description");
      database.startAnalysis(assetId, "run-1", "openai", "gpt-5.6-terra", .05);
      const analyzed = database.completeAnalysis(assetId, "run-1", {
        description: "AI sees a boss fight",
        tags: ["gameplay", "boss"],
        segments: [{
          start_ms: 0,
          end_ms: 30_000,
          description: "Character approaches a boss",
          observed_label: "Dodging an attack",
          evidence_spacing_sec: .5,
          handoff_reason: "",
          tags: ["boss"],
          confidence: .9,
          motion_level: .7,
          visual_category: "gameplay",
          suitability: "Good establishing B-roll",
        }],
        provider: "openai",
        model: "gpt-5.6-terra",
        prompt_version: "media-analysis-v1",
        sample_count: 3,
        input_tokens: 100,
        output_tokens: 50,
        cost_usd: .001,
      });
      expect(analyzed.analysisState).toBe("ready");
      expect(analyzed.aiDescription).toBe("AI sees a boss fight");
      expect(analyzed.effectiveDescription).toBe("My authoritative description");
      expect(analyzed.segments[0]).toMatchObject({ startMs: 0, endMs: 30_000, visualCategory: "gameplay",
        observedLabel: "Dodging an attack", evidenceSpacingSec: .5 });
      expect(database.listAssets({ query: "Dodging" }).assets[0].id).toBe(assetId);
      database.addUserSegment(assetId, { startMs: 10_000, endMs: 20_000 }, "My scene");
      expect(database.getAsset(assetId).segments.filter((segment) => segment.origin === "ai")
        .every((segment) => segment.observedLabel === "Dodging an attack" && segment.evidenceSpacingSec === .5)).toBe(true);
    } finally {
      database.close();
    }
  });

  it("keeps file descriptions beside non-overlapping user segments and protects them from AI analysis", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      const token = database.beginScan(mediaRoot.id);
      const assetId = database.upsertIndexedFile(mediaRoot.id, token, mediaFile(path.join(root, "media", "trailer.mp4")));
      database.finishScan(mediaRoot.id, token);
      database.updateUserDescription(assetId, "A compilation of game trailers");
      database.addUserSegment(assetId, { startMs: 10_000, endMs: 20_000 }, "Combat trailer section");

      expect(() => database.addUserSegment(
        assetId,
        { startMs: 15_000, endMs: 25_000 },
        "Overlapping description",
      )).toThrow(/overlaps/i);

      database.startAnalysis(assetId, "run-1", "openai", "gpt-5.6-terra", .05);
      const analyzed = database.completeAnalysis(assetId, "run-1", {
        description: "AI summary",
        tags: ["trailer"],
        segments: [{
          start_ms: 0,
          end_ms: 30_000,
          description: "General trailer footage",
          tags: ["trailer"],
          confidence: .8,
          motion_level: .6,
          visual_category: "trailer",
          suitability: "General B-roll",
        }],
        provider: "openai",
        model: "gpt-5.6-terra",
        prompt_version: "media-analysis-v6",
        sample_count: 3,
        input_tokens: 100,
        output_tokens: 50,
        cost_usd: .001,
      });

      expect(analyzed.userDescription).toBe("A compilation of game trailers");
      expect(analyzed.segments.map((segment) => ({
        range: [segment.startMs, segment.endMs],
        origin: segment.origin,
      }))).toEqual([
        { range: [0, 10_000], origin: "ai" },
        { range: [10_000, 20_000], origin: "user" },
        { range: [20_000, 30_000], origin: "ai" },
      ]);
    } finally {
      database.close();
    }
  });

  it("tracks subdirectories explicitly and bulk-untracks only the selected direct files", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      expect(database.listDirectoryScopes(mediaRoot.id)).toEqual([""]);
      database.setDirectoryIncluded(mediaRoot.id, "", false);
      expect(database.listDirectoryScopes(mediaRoot.id)).toEqual([]);
      database.setDirectoryIncluded(mediaRoot.id, "", true);
      expect(database.listDirectoryScopes(mediaRoot.id)).toEqual([""]);
      database.setDirectoryIncluded(mediaRoot.id, "frames", true);
      expect(database.listDirectoryScopes(mediaRoot.id)).toEqual(["", "frames"]);

      const token = database.beginScan(mediaRoot.id);
      const rootVideo = database.upsertIndexedFile(
        mediaRoot.id,
        token,
        { ...mediaFile(path.join(root, "media", "video.mp4")), relativePath: "video.mp4" },
      );
      database.upsertIndexedFile(
        mediaRoot.id,
        token,
        {
          ...mediaFile(path.join(root, "media", "frames", "frame-1.png")),
          relativePath: path.join("frames", "frame-1.png"),
          mediaKind: "image",
        },
      );
      const nestedImage = database.upsertIndexedFile(
        mediaRoot.id,
        token,
        {
          ...mediaFile(path.join(root, "media", "frames", "selected", "frame-2.png")),
          relativePath: path.join("frames", "selected", "frame-2.png"),
          mediaKind: "image",
        },
      );
      database.finishScan(mediaRoot.id, token, ["", "frames", path.join("frames", "selected")]);

      database.setDirectoryVisible(mediaRoot.id, "frames", "subtree", false);
      expect(database.listAssets({ rootId: mediaRoot.id }).assets.map((asset) => asset.id)).toEqual([rootVideo]);
      database.setDirectoryVisible(mediaRoot.id, "frames", "subtree", true);
      expect(database.listAssets({ rootId: mediaRoot.id }).total).toBe(3);
      database.setDirectoryVisible(mediaRoot.id, "frames", "direct", false);
      expect(new Set(database.listAssets({ rootId: mediaRoot.id }).assets.map((asset) => asset.id))).toEqual(
        new Set([rootVideo, nestedImage]),
      );
      database.setDirectoryVisible(mediaRoot.id, "frames", "direct", true);

      expect(database.removeDirectoryAssets(mediaRoot.id, "frames").removedAssets).toBe(1);
      expect(database.getAsset(rootVideo).availability).toBe("active");
      expect(database.listDirectoryScopes(mediaRoot.id)).toEqual([""]);
      expect(database.listAssets({ rootId: mediaRoot.id }).total).toBe(2);
    } finally {
      database.close();
    }
  });

  it("persists hidden directory UI state independently of catalog visibility", () => {
    const root = temporaryRoot();
    const database = new MediaLibraryDatabase(path.join(root, "library.sqlite3"));
    try {
      const mediaRoot = database.addRoot(path.join(root, "media"), "referenced");
      expect(database.directoryHiddenStates(mediaRoot.id)).toEqual([]);
      database.setDirectoryHidden(mediaRoot.id, "frame-dumps", true);
      expect(database.directoryHiddenStates(mediaRoot.id)).toEqual([
        { relativeDirectory: "frame-dumps", hidden: true },
      ]);
      database.setDirectoryHidden(mediaRoot.id, "frame-dumps", false);
      expect(database.directoryHiddenStates(mediaRoot.id)).toEqual([
        { relativeDirectory: "frame-dumps", hidden: false },
      ]);
    } finally {
      database.close();
    }
  });

  it("migrates the original schema to purpose-aware directory scopes", () => {
    const root = temporaryRoot();
    const databasePath = path.join(root, "library.sqlite3");
    const legacy = new DatabaseSync(databasePath);
    legacy.exec(`
      CREATE TABLE library_roots (
        id TEXT PRIMARY KEY, canonical_path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
        recursive INTEGER NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
        last_scan_at TEXT NOT NULL, last_scan_status TEXT NOT NULL, last_error TEXT NOT NULL
      );
      CREATE TABLE assets (
        id TEXT PRIMARY KEY, root_id TEXT NOT NULL, relative_path TEXT NOT NULL
      );
      INSERT INTO library_roots VALUES ('root', 'C:\\media', 'referenced', 1, 1, '', '', 'complete', '');
      INSERT INTO assets VALUES ('asset', 'root', 'frames\\frame.png');
      PRAGMA user_version=1;
    `);
    legacy.close();

    const database = new MediaLibraryDatabase(databasePath);
    try {
      expect(database.listRoots()[0]).toMatchObject({ purpose: "user", recursive: false });
      expect(database.directoryTrackedCounts("root")).toEqual({ frames: 1 });
      expect(database.listDirectoryScopes("root")).toEqual([""]);
    } finally {
      database.close();
    }
  });
});

function temporaryRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subutl-library-"));
  roots.push(root);
  fs.mkdirSync(path.join(root, "media"), { recursive: true });
  return root;
}

function mediaFile(canonicalPath: string): IndexedMediaFile {
  return {
    canonicalPath,
    relativePath: path.relative(path.dirname(path.dirname(canonicalPath)), canonicalPath),
    mediaKind: "video",
    sizeBytes: 1234,
    mtimeNs: "1000000",
    quickFingerprint: "a".repeat(64),
    durationMs: 60_000,
    width: 1920,
    height: 1080,
    frameRateNum: 60,
    frameRateDen: 1,
    videoCodec: "h264",
    audioCodec: "aac",
    hasAudio: true,
    transparency: "unsupported",
    availability: "active",
    error: "",
  };
}
