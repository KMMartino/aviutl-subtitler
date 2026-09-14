import { publishDeliverables } from "./projectDeliverables";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import type { CreatorProject, ProjectCatalog, ProjectRecording, ProjectResult, ProjectUpdate, TranscriptPreview } from "../shared/creatorProject";
import { projectSourceRevision } from "../shared/creatorProject";
import { speechPath } from "../shared/creatorProject";
import type { WorkflowName } from "../renderer/lib/types";

const manifestName = "subutl-project.json";

function transcriptFiles(directory: string, depth = 0): string[] {
  if (depth > 8 || !fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? transcriptFiles(file, depth + 1) : entry.isFile() && entry.name.endsWith(".transcript.json") ? [file] : [];
  });
}

function inspectRecordings(recordings: ProjectRecording[]): ProjectRecording[] {
  return recordings.map((recording) => ({ ...recording, mediaIdentity: Object.fromEntries(
    [...new Set([recording.source.audioPath, recording.source.visualPath])].map((file) => {
      try { const stat = fs.statSync(file); return [file, stat.isFile() ? { sizeBytes: stat.size, modifiedMs: stat.mtimeMs } : null]; }
      catch { return [file, null]; }
    }),
  ) }));
}

function writeJson(file: string, value: unknown): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${crypto.randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), { flag: "wx" });
    fs.renameSync(temporary, file);
  } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
}

/** Manifests own project data; the AppData catalog is only a recent-project index. */
export class CreatorProjectStore {
  private readonly catalogFile: string;
  private readonly activeResults = new Set<string>();
  constructor(stateDirectory: string, private readonly initialDirectory: string) {
    this.catalogFile = path.join(stateDirectory, "creator-projects.json");
  }

  catalog(): ProjectCatalog {
    if (!fs.existsSync(this.catalogFile)) return { defaultDirectory: this.initialDirectory, recent: [] };
    return JSON.parse(fs.readFileSync(this.catalogFile, "utf8")) as ProjectCatalog;
  }

  findReviewedResult(file: string): { project: CreatorProject; result: ProjectResult } {
    const media = new Set(Array.from(new TextDecoder("shift_jis").decode(fs.readFileSync(file)).matchAll(/^file=(.+)$/gm), (match) => match[1].trim().toLowerCase()));
    const matches: { project: CreatorProject; result: ProjectResult }[] = [];
    for (const entry of this.catalog().recent) {
      if (!fs.existsSync(path.join(entry.directory, manifestName))) continue;
      const project = this.open(entry.directory);
      for (const result of project.results) {
        if (result.workflow !== "hosted-long-stream" || result.status !== "complete" || result.kind || !fs.existsSync(result.outputPath)) continue;
        const checkpoint = JSON.parse(fs.readFileSync(result.outputPath, "utf8"));
        const sources: { source_id: string; visual_path: string }[] = checkpoint.sources ?? [];
        const present = sources.filter((source) => media.has(source.visual_path.toLowerCase())).map((source) => source.source_id);
        const groups: string[][] = checkpoint.outputs?.exo_parts?.map((part: { source_ids: string[] }) => part.source_ids) ?? [sources.map((source) => source.source_id)];
        if (present.length && groups.some((group) => group.length === present.length && group.every((id) => present.includes(id)))) matches.push({ project, result });
      }
    }
    const exact = matches.filter(({ result }) => (result.deliverablePaths ?? [result.deliverablePath]).some((output) => output && path.resolve(output).toLowerCase() === path.resolve(file).toLowerCase()));
    if (exact.length === 1) return exact[0];
    if (matches.length === 1) return matches[0];
    throw new Error(matches.length ? "Several results match this EXO. Open its project and use Apply reviewed EXO on the intended result." : "Open the project that created this EXO, then use Apply reviewed EXO on its result.");
  }

  setDefaultDirectory(directory: string): ProjectCatalog {
    if (!path.isAbsolute(directory)) throw new Error("Choose an absolute project directory.");
    const catalog = { ...this.catalog(), defaultDirectory: directory };
    writeJson(this.catalogFile, catalog);
    return catalog;
  }

  create(name: string, parentDirectory?: string): CreatorProject {
    const cleanName = Array.from(name.trim(), (character) => character.charCodeAt(0) < 32 ? "-" : character).join("").replace(/[<>:"/\\|?*]/g, "-").replace(/[. ]+$/, "").slice(0, 100) || "Untitled project";
    const parent = parentDirectory || this.catalog().defaultDirectory;
    if (!path.isAbsolute(parent)) throw new Error("Choose an absolute project directory.");
    fs.mkdirSync(parent, { recursive: true });
    let directory = path.join(parent, cleanName);
    for (let suffix = 2; ; suffix++) {
      try { fs.mkdirSync(directory); break; }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
      directory = path.join(parent, `${cleanName} (${suffix})`);
    }
    const project: CreatorProject = {
      schemaVersion: 1, id: crypto.randomUUID(), name: cleanName, directory, revision: 0,
      updatedAt: new Date().toISOString(), recordings: [], results: [],
      editorial: { titleOrGame: cleanName, objective: "", targetDurationMinSeconds: 60, targetDurationMaxSeconds: 60, outputLocale: "en" },
    };
    for (const child of ["media", "results", "exports"]) fs.mkdirSync(path.join(directory, child));
    return this.persist(project);
  }

  open(directory: string): CreatorProject {
    const project = JSON.parse(fs.readFileSync(path.join(directory, manifestName), "utf8")) as CreatorProject;
    if (project.schemaVersion !== 1 || !project.id || !Array.isArray(project.recordings) || !Array.isArray(project.results)) throw new Error("Unsupported SubUtl project manifest.");
    project.directory = path.resolve(directory);
    const recordings = inspectRecordings(project.recordings);
    const sourcesChanged = JSON.stringify(recordings) !== JSON.stringify(project.recordings);
    project.recordings = recordings;
    let interrupted = false;
    for (const result of project.results) {
      if (result.status === "running" && !this.activeResults.has(result.id)) { result.status = "interrupted"; interrupted = true; }
    }
    if (interrupted || sourcesChanged) return this.persist(project);
    this.remember(project);
    return project;
  }

  update(update: ProjectUpdate): CreatorProject {
    const project = this.open(update.directory);
    if (project.revision !== update.revision) throw new Error("Project changed. Reopen it before saving.");
    if (project.results.some((result) => result.status === "running")) throw new Error("Wait for the active task before changing project sources.");
    const paths = update.recordings.flatMap((recording) => [...new Set([recording.source.audioPath, recording.source.visualPath])].map((file) => path.resolve(file).toLowerCase()));
    if (new Set(paths).size !== paths.length) throw new Error("A source file is already in this project.");
    const ids = update.recordings.map((recording) => recording.id);
    if (new Set(ids).size !== ids.length || ids.some((id) => !id)) throw new Error("Recording identities must be unique.");
    const { titleOrGame, objective, targetDurationMinSeconds, targetDurationMaxSeconds, outputLocale, processingLocale } = update.editorial;
    if (update.outputDirectory && !path.isAbsolute(update.outputDirectory)) throw new Error("Choose an absolute output directory.");
    return this.persist({ ...project, outputDirectory: update.outputDirectory || undefined, name: update.name.trim() || project.name, recordings: inspectRecordings(update.recordings), editorial: { titleOrGame, objective, targetDurationMinSeconds, targetDurationMaxSeconds, outputLocale, processingLocale } });
  }

  begin(directory: string, workflow: WorkflowName, recordingId: string | null, parentResultId?: string): { project: CreatorProject; result: ProjectResult } {
    const project = this.open(directory);
    if (project.results.some((result) => result.status === "running")) throw new Error("A project task is already running.");
    if (!project.recordings.length || (recordingId && !project.recordings.some((item) => item.id === recordingId))) throw new Error("Select a recording first.");
    const id = crypto.randomUUID();
    const resultDirectory = path.join(directory, "results", id);
    fs.mkdirSync(resultDirectory, { recursive: true });
    const source = (project.recordings.find((recording) => recording.id === recordingId) ?? project.recordings[0]).source.visualPath;
    const outputDirectory = project.outputDirectory || path.join(path.dirname(source), path.parse(source).name);
    fs.mkdirSync(outputDirectory, { recursive: true });
    const stem = workflow === "hosted-long-stream" ? "editing-guide" : "subtitles";
    let deliverablePath = path.join(outputDirectory, `${stem}.exo`);
    for (let suffix = 2; fs.existsSync(deliverablePath) || fs.existsSync(deliverablePath.replace(/\.exo$/, ".html"))
      || project.results.some((result) => result.deliverablePath === deliverablePath); suffix++) {
      deliverablePath = path.join(outputDirectory, `${stem}-${suffix}.exo`);
    }
    const parent = parentResultId ? project.results.find((result) => result.id === parentResultId) : project.results.slice().reverse().find((result) => result.workflow === workflow && !result.kind && result.recordingId === recordingId && result.status === "complete");
    const result: ProjectResult = {
      id, workflow, recordingId, sourceRevision: parentResultId && parent ? parent.sourceRevision : projectSourceRevision(project, recordingId), createdAt: new Date().toISOString(), status: "running",
      outputPath: workflow === "hosted-long-stream" ? path.join(resultDirectory, "editing-guide.json") : path.join(resultDirectory, "subtitles.exo"),
      deliverablePath,
      sidecarDir: path.join(resultDirectory, "artifacts"), ...(parent ? { parentId: parent.id } : {}),
      editorialRevision: JSON.stringify(project.editorial),
      ...(parentResultId ? { kind: "reviewed-exo" as const } : {}),
    };
    project.results.push(result);
    this.activeResults.add(id);
    return { project: this.persist(project), result };
  }

  resume(directory: string, resultId: string): { project: CreatorProject; result: ProjectResult } {
    const project = this.open(directory);
    if (project.results.some((result) => result.status === "running")) throw new Error("A project task is already running.");
    const result = project.results.find((result) => result.id === resultId && result.status !== "complete" && !result.kind);
    if (!result) throw new Error("Select an interrupted or failed task to resume.");
    if (result.sourceRevision !== projectSourceRevision(project, result.recordingId)) throw new Error("Sources changed. Start a new result instead of resuming this task.");
    result.status = "running";
    this.activeResults.add(result.id);
    return { project: this.persist(project), result };
  }

  async finish(directory: string, id: string, status: ProjectResult["status"], reviewedOutput?: string): Promise<CreatorProject> {
    const project = this.open(directory);
    const result = project.results.find((item) => item.id === id);
    if (!result) throw new Error("Unknown project result.");
    result.status = status;
    const transcripts = transcriptFiles(result.sidecarDir);
    result.transcripts = transcripts.length ? transcripts : project.results.find((parent) => parent.id === result.parentId)?.transcripts ?? [];
    if (reviewedOutput) { result.outputPath = reviewedOutput; result.kind = "reviewed-exo"; }
    try {
      if (status === "complete" && result.deliverablePath) {
        result.deliverablePaths = await publishDeliverables(result.outputPath.replace(/\.json$/i, ".exo"), result.deliverablePath);
      }
    } catch (error) {
      result.status = "failed";
      this.activeResults.delete(id);
      this.persist(project);
      throw error;
    }
    this.activeResults.delete(id);
    return this.persist(project);
  }

  transcript(directory: string, resultId: string, file: string): TranscriptPreview {
    const project = this.open(directory);
    const result = project.results.find((result) => result.id === resultId);
    if (!result?.transcripts?.includes(file)) throw new Error("This transcript is not part of the selected result.");
    const document = JSON.parse(fs.readFileSync(file, "utf8"));
    if (document.type !== "aligned_transcript" || !Array.isArray(document.backend?.segments)) throw new Error("Unsupported transcript artifact.");
    return { revision: document.revision_id, source: document.source_path, segments: document.backend.segments.map((segment: { start: number; end: number; text: string }) => ({ start: segment.start, end: segment.end, text: segment.text })) };
  }

  exportExo(directory: string, resultId: string): string {
    const project = this.open(directory);
    const result = project.results.find((result) => result.id === resultId && result.status === "complete");
    if (!result) throw new Error("Select a completed result to export.");
    if (result.deliverablePath && fs.existsSync(result.deliverablePath)) return result.deliverablePath;
    const source = result.outputPath.replace(/\.json$/, ".exo");
    if (path.extname(source).toLowerCase() !== ".exo") throw new Error("This result has no EXO export.");
    const destination = path.join(project.directory, "exports", `${result.workflow}-${crypto.randomUUID()}.exo`);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    return destination;
  }

  seedEditorialResult(project: CreatorProject, result: ProjectResult): boolean {
    const previous = project.results.slice().reverse().find((candidate) => candidate.id !== result.id
      && candidate.workflow === "hosted-long-stream" && !candidate.kind && candidate.sourceRevision === result.sourceRevision
      && candidate.editorialRevision === result.editorialRevision && fs.existsSync(candidate.outputPath));
    if (!previous) return false;
    fs.copyFileSync(previous.outputPath, result.outputPath, fs.constants.COPYFILE_EXCL);
    const operations = previous.outputPath.replace(/\.json$/, ".operations");
    if (fs.existsSync(operations)) fs.cpSync(operations, result.outputPath.replace(/\.json$/, ".operations"), { recursive: true, errorOnExist: true, force: false });
    const silenceCache = path.join(previous.sidecarDir, "silence-operations");
    if (fs.existsSync(silenceCache)) fs.cpSync(silenceCache, path.join(result.sidecarDir, "silence-operations"), { recursive: true, errorOnExist: true, force: false });
    return true;
  }

  reusableTranscripts(project: CreatorProject, recordingId: string | null, audioTrack: number): string[] {
    const wanted = new Map(project.recordings.filter((recording) => !recordingId || recording.id === recordingId).map((recording) => [path.resolve(speechPath(recording.source)).toLowerCase(), recording.source.mode === "paired" ? 0 : audioTrack]));
    const found = new Map<string, string>();
    const visit = (directory: string) => {
      for (const file of transcriptFiles(directory)) {
        try {
          const document = JSON.parse(fs.readFileSync(file, "utf8"));
          if (document.type !== "aligned_transcript" || document.backend?.status !== "ok" || typeof document.source_path !== "string") continue;
          const source = path.resolve(document.source_path).toLowerCase();
          if (!wanted.has(source) || document.audio_track !== wanted.get(source) || found.has(source)) continue;
          const stat = fs.statSync(document.source_path);
          if (stat.size !== document.source_fingerprint?.size_bytes || Math.abs(stat.mtimeMs - document.source_modified_ns / 1_000_000) > 0.01) continue;
          // Python verifies the complete document and media fingerprint before reuse.
          found.set(source, file);
        } catch { /* A missing or incomplete artifact is never a reuse candidate. */ }
      }
    };
    for (const result of project.results.slice().reverse()) visit(result.sidecarDir);
    return [...found.values()];
  }

  private persist(project: CreatorProject): CreatorProject {
    project.revision++;
    project.updatedAt = new Date().toISOString();
    writeJson(path.join(project.directory, manifestName), project);
    this.remember(project);
    return project;
  }

  private remember(project: CreatorProject): void {
    const catalog = this.catalog();
    catalog.recent = [{ name: project.name, directory: project.directory, updatedAt: project.updatedAt }, ...catalog.recent.filter((item) => item.directory !== project.directory)].slice(0, 40);
    writeJson(this.catalogFile, catalog);
  }
}
