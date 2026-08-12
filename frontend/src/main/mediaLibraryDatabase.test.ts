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
      expect(analyzed.segments[0]).toMatchObject({ startMs: 0, endMs: 30_000, visualCategory: "gameplay" });
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
