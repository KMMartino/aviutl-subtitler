import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CreatorProjectStore } from "./creatorProjectStore";
import { buildEditorialSources } from "../renderer/lib/editorialPairing";
import { projectSourceRevision } from "../shared/creatorProject";

describe("creator projects", () => {
  let root: string;
  let store: CreatorProjectStore;
  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), "subutl-project-test-"));
    store = new CreatorProjectStore(path.join(root, "state"), path.join(root, "projects"));
  });
  afterEach(() => fs.rmSync(root, { recursive: true, force: true }));
  function populated() {
    const project = store.create("Gameplay session");
    const source = buildEditorialSources([{ path: path.join(root, "game.mp4"), analysis: { durationSeconds: 60, width: 1920, height: 1080, averageFrameRate: 60, nominalFrameRate: 60, frameRateMode: "reported-cfr", formatName: "mp4", videoCodec: "h264", thumbnailDataUrl: "", audioTracks: [] } }])[0];
    return store.update({ ...project, recordings: [{ id: "recording-1", source }] });
  }
  it("deletes only the project folder and removes its recent entry", async () => {
    const project = populated();
    const external = project.recordings[0].source.visualPath;
    fs.writeFileSync(external, "external recording");
    fs.mkdirSync(path.join(project.directory, "Sources"));
    fs.writeFileSync(path.join(project.directory, "Sources", "download.mp4"), "download");
    const trashed = path.join(root, "recycle-bin-project");
    await store.delete(project.directory, async directory => { fs.renameSync(directory, trashed); });
    expect(fs.existsSync(project.directory)).toBe(false);
    expect(fs.readFileSync(external, "utf8")).toBe("external recording");
    expect(fs.existsSync(path.join(trashed, "Sources", "download.mp4"))).toBe(true);
    expect(store.catalog().recent).toEqual([]);
  });
  it("keeps the catalog if deletion fails and rejects active or unregistered projects", async () => {
    const project = populated();
    await expect(store.delete(project.directory, async () => { throw new Error("in use"); })).rejects.toThrow("in use");
    expect(store.catalog().recent).toHaveLength(1);
    store.begin(project.directory, "hosted", "recording-1");
    await expect(store.delete(project.directory, async () => { throw new Error("must not trash"); })).rejects.toThrow("active task");
    await expect(store.delete(root, async () => { throw new Error("must not trash"); })).rejects.toThrow("registered project");
  });
  async function complete(directory: string, id: string) {
    const result = store.open(directory).results.find((item) => item.id === id)!;
    const exo = result.outputPath.replace(/\.json$/, ".exo");
    if (!fs.existsSync(exo)) fs.writeFileSync(exo, "generated EXO");
    return store.finish(directory, id, "complete");
  }
  it("stores projects outside app state and preserves independent revisions", async () => {
    const project = populated();
    const first = store.begin(project.directory, "hosted", "recording-1");
    fs.writeFileSync(first.result.outputPath, "original export");
    await complete(project.directory, first.result.id);
    const second = store.begin(project.directory, "hosted", "recording-1");
    expect(second.result.outputPath).not.toBe(first.result.outputPath);
    expect(second.result.parentId).toBe(first.result.id);
    await store.finish(project.directory, second.result.id, "failed");
    expect(fs.readFileSync(first.result.outputPath, "utf8")).toBe("original export");
    expect(store.open(project.directory).results.map((result) => result.status)).toEqual(["complete", "failed"]);
    expect(store.catalog().recent[0].directory).toBe(project.directory);
    const exported = store.exportExo(project.directory, first.result.id);
    expect(path.dirname(exported)).toBe(path.join(root, "game"));
    expect(fs.readFileSync(exported, "utf8")).toBe("original export");
  });
  it("recovers interrupted tasks after restart and rejects stale edits", async () => {
    const project = populated();
    store.begin(project.directory, "hosted", "recording-1");
    expect(() => store.update(project)).toThrow();
    const reopened = new CreatorProjectStore(path.join(root, "state"), path.join(root, "projects")).open(project.directory);
    expect(reopened.results[0].status).toBe("interrupted");
    expect(() => store.update(project)).toThrow("Project changed");
    const restartedStore = new CreatorProjectStore(path.join(root, "state"), path.join(root, "projects"));
    const resumed = restartedStore.resume(project.directory, reopened.results[0].id);
    expect(resumed.result.id).toBe(reopened.results[0].id);
    expect(resumed.result.outputPath).toBe(reopened.results[0].outputPath);
    expect(resumed.project.results).toHaveLength(1);
  });
  it("keeps editorial work managed and exports beside media or to a chosen folder without overwriting", async () => {
    const project = populated();
    const first = store.begin(project.directory, "hosted-long-stream", null);
    expect(path.dirname(first.result.deliverablePath!)).toBe(path.join(root, "game"));
    expect(first.result.outputPath.startsWith(project.directory)).toBe(true);
    expect(first.result.sidecarDir.startsWith(project.directory)).toBe(true);
    const managedHtml = first.result.outputPath.replace(/\.json$/, ".html");
    fs.mkdirSync(path.join(path.dirname(managedHtml), "frames"));
    fs.writeFileSync(path.join(path.dirname(managedHtml), "frames", "scene.jpg"), "image");
    fs.writeFileSync(managedHtml, '<img src="frames/scene.jpg">');
    fs.writeFileSync(first.result.outputPath, '{}');
    const part = first.result.outputPath.replace(/\.json$/, "-part-02.exo");
    fs.writeFileSync(part, "second EXO");
    fs.writeFileSync(first.result.outputPath, JSON.stringify({ outputs: { exo_parts: [{ path: first.result.outputPath.replace(/\.json$/, ".exo") }, { path: part }] } }));
    const finished = await complete(project.directory, first.result.id);
    expect(finished.results[0].deliverablePaths).toHaveLength(3);
    expect(fs.readFileSync(first.result.deliverablePath!.replace(/\.exo$/, "-part-02.exo"), "utf8")).toBe("second EXO");
    const publishedHtml = first.result.deliverablePath!.replace(/\.exo$/, ".html");
    expect(fs.readFileSync(publishedHtml, "utf8")).toContain('editing-guide-assets/frames/scene.jpg');
    expect(fs.readFileSync(path.join(path.dirname(publishedHtml), "editing-guide-assets", "frames", "scene.jpg"), "utf8")).toBe("image");
    expect(fs.existsSync(path.join(path.dirname(publishedHtml), "editing-guide.json"))).toBe(false);
    fs.writeFileSync(first.result.deliverablePath!, "user edit");
    const second = store.begin(project.directory, "hosted-long-stream", null);
    expect(second.result.deliverablePath).not.toBe(first.result.deliverablePath);
    const stopped = await store.finish(project.directory, second.result.id, "cancelled");
    const custom = store.update({ ...stopped, outputDirectory: path.join(root, "chosen") });
    const third = store.begin(custom.directory, "hosted", "recording-1");
    expect(path.dirname(third.result.deliverablePath!)).toBe(custom.outputDirectory);
    expect(third.result.outputPath.startsWith(custom.directory)).toBe(true);
    expect(fs.readFileSync(first.result.deliverablePath!, "utf8")).toBe("user edit");
    expect(store.exportExo(finished.directory, first.result.id)).toBe(first.result.deliverablePath);
  });
  it("finds a renamed second EXO part through the managed project", async () => {
    const project = populated();
    const run = store.begin(project.directory, "hosted-long-stream", null);
    const one = path.join(root, "1.game.mp4"), two = path.join(root, "2.game.mp4");
    const part = run.result.outputPath.replace(/\.json$/, "-part-02.exo");
    fs.writeFileSync(part, `[exedit]\nfile=${two}\n`);
    fs.writeFileSync(run.result.outputPath, JSON.stringify({ sources: [{ source_id: "one", visual_path: one }, { source_id: "two", visual_path: two }], outputs: { exo_parts: [{ path: run.result.outputPath.replace(/\.json$/, ".exo"), source_ids: ["one"] }, { path: part, source_ids: ["two"] }] } }));
    await complete(project.directory, run.result.id);
    const reviewed = path.join(root, "reviewed.exo");
    fs.copyFileSync(part, reviewed);
    expect(store.findReviewedResult(reviewed).result.id).toBe(run.result.id);
    fs.writeFileSync(reviewed, "file=unrelated.mp4");
    expect(() => store.findReviewedResult(reviewed)).toThrow("Open the project");
  });

  it("tracks source changes without changing prior results", async () => {
    const project = populated();
    const first = store.begin(project.directory, "hosted", "recording-1");
    const finished = await complete(project.directory, first.result.id);
    const changed = store.update({ ...finished, recordings: finished.recordings.map((recording) => ({ ...recording, source: { ...recording.source, speechSource: "gameplay" } })) });
    expect(changed.results[0].sourceRevision).not.toBe(projectSourceRevision(changed, "recording-1"));
    expect(changed.results[0].status).toBe("complete");
  });
  it("never overwrites an existing project with the same name", async () => {
    const first = store.create("Session");
    const second = store.create("Session");
    expect(second.directory).not.toBe(first.directory);
    expect(store.open(first.directory).id).toBe(first.id);
  });
  it("clones compatible editorial checkpoints without changing their originals", async () => {
    const project = populated();
    const first = store.begin(project.directory, "hosted-long-stream", null);
    fs.writeFileSync(first.result.outputPath, '{"saved":"boundaries"}');
    const operations = first.result.outputPath.replace(/\.json$/, ".operations");
    fs.mkdirSync(operations); fs.writeFileSync(path.join(operations, "result.json"), "immutable operation");
    await complete(project.directory, first.result.id);
    const second = store.begin(project.directory, "hosted-long-stream", null);
    expect(store.seedEditorialResult(second.project, second.result)).toBe(true);
    fs.writeFileSync(second.result.outputPath, "successor");
    expect(fs.readFileSync(first.result.outputPath, "utf8")).toBe('{"saved":"boundaries"}');
    expect(fs.readFileSync(path.join(second.result.outputPath.replace(/\.json$/, ".operations"), "result.json"), "utf8")).toBe("immutable operation");
  });
  it("finds reusable transcripts across tasks and rejects changed media", async () => {
    const project = populated();
    const source = project.recordings[0].source.audioPath;
    fs.writeFileSync(source, "media");
    const run = store.begin(project.directory, "hosted", "recording-1");
    fs.mkdirSync(run.result.sidecarDir);
    const transcript = path.join(run.result.sidecarDir, "source.transcript.json");
    fs.writeFileSync(transcript, JSON.stringify({ type: "aligned_transcript", source_path: source, audio_track: 0, source_modified_ns: fs.statSync(source).mtimeMs * 1_000_000, source_fingerprint: { size_bytes: 5 }, revision_id: "transcript-1", backend: { status: "ok", segments: [{ start: 0, end: 1, text: "Example" }] } }));
    const finished = await complete(project.directory, run.result.id);
    expect(store.reusableTranscripts(finished, null, 0)).toEqual([transcript]);
    expect(store.transcript(project.directory, run.result.id, transcript).segments[0].text).toBe("Example");
    expect(() => store.transcript(project.directory, run.result.id, path.join(root, "unrelated.json"))).toThrow();
    expect(store.reusableTranscripts(finished, null, 1)).toEqual([]);
    fs.appendFileSync(source, "changed");
    expect(store.reusableTranscripts(finished, null, 0)).toEqual([]);
  });
});
