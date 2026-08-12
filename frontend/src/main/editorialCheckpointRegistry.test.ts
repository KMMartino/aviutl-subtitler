import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { RuntimePaths } from "./paths";
import { listEditorialCheckpoints, registerEditorialCheckpoint, removeEditorialCheckpoint } from "./editorialCheckpointRegistry";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("editorial checkpoint registry", () => {
  it("registers, summarizes, and removes durable projects", () => {
    const paths = makePaths();
    const media = path.join(paths.stateRoot, "captures", "part.mp4");
    const checkpoint = path.join(path.dirname(media), "part-editorial.json");
    fs.mkdirSync(path.dirname(checkpoint), { recursive: true });
    fs.writeFileSync(checkpoint, JSON.stringify({
      title_or_game: "Challenge run",
      objective: "Finish the game",
      updated_at_utc: "2026-08-05T12:00:00Z",
      sources: [{ source_id: "one" }],
      editorial_map: { status: "complete" },
    }), "utf8");

    registerEditorialCheckpoint(paths, checkpoint);
    expect(listEditorialCheckpoints(paths)).toEqual([expect.objectContaining({
      path: checkpoint,
      title: "Challenge run",
      status: "complete",
      sourceCount: 1,
    })]);

    removeEditorialCheckpoint(paths, checkpoint);
    expect(listEditorialCheckpoints(paths, media)).toEqual([]);
  });

  it("discovers a checkpoint beside the last selected recording", () => {
    const paths = makePaths();
    const media = path.join(paths.stateRoot, "captures", "part.mp4");
    const checkpoint = path.join(path.dirname(media), "part-editorial.json");
    fs.mkdirSync(path.dirname(checkpoint), { recursive: true });
    fs.writeFileSync(checkpoint, JSON.stringify({ title_or_game: "Run", objective: "Review", sources: [{}], editorial_map: { status: "failed" } }), "utf8");

    expect(listEditorialCheckpoints(paths, media)[0]).toMatchObject({ path: checkpoint, status: "failed" });
  });
});

function makePaths(): RuntimePaths {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-editorial-registry-"));
  roots.push(root);
  const stateRoot = path.join(root, "state");
  return {
    appResourceRoot: root,
    bundledBackendRoot: root,
    bundledConfigRoot: path.join(root, "configs"),
    userDataRoot: stateRoot,
    stateRoot,
    userConfigRoot: path.join(stateRoot, "configs"),
    userToolsRoot: path.join(stateRoot, "tools"),
    userModelsRoot: path.join(stateRoot, "models"),
    managedPythonRoot: path.join(stateRoot, "python"),
    managedFfmpegRoot: path.join(stateRoot, "tools", "ffmpeg"),
    managedYtDlpRoot: path.join(stateRoot, "tools", "yt-dlp"),
    mediaLibraryRoot: path.join(stateRoot, "media-library"),
    mediaLibraryDatabase: path.join(stateRoot, "media-library", "library.sqlite3"),
    managedMediaRoot: path.join(root, "videos"),
    managedWebMediaRoot: path.join(root, "web-videos"),
    envFile: path.join(stateRoot, ".env"),
    glossaryFile: path.join(stateRoot, "glossary.txt"),
  };
}
