import type { EditorialProjectRequest, EditorialSourceSelection, WorkflowName } from "../renderer/lib/types";

export type ProjectResult = {
  id: string;
  workflow: WorkflowName;
  recordingId: string | null;
  sourceRevision: string;
  createdAt: string;
  status: "running" | "complete" | "failed" | "cancelled" | "interrupted";
  outputPath: string;
  deliverablePath?: string;
  deliverablePaths?: string[];
  sidecarDir: string;
  parentId?: string;
  editorialRevision?: string;
  kind?: "reviewed-exo";
  transcripts?: string[];
};

export function speechPath(source: EditorialSourceSelection): string { return source.speechSource === "gameplay" ? source.visualPath : source.audioPath; }

export type ProjectRecording = {
  id: string;
  source: EditorialSourceSelection;
  mediaIdentity?: Record<string, { sizeBytes: number; modifiedMs: number } | null>;
};
export type CreatorProject = {
  schemaVersion: 1;
  id: string;
  name: string;
  directory: string;
  outputDirectory?: string;
  revision: number;
  updatedAt: string;
  recordings: ProjectRecording[];
  editorial: Omit<EditorialProjectRequest, "sources">;
  results: ProjectResult[];
};
export type ProjectSummary = Pick<CreatorProject, "name" | "directory" | "updatedAt">;
export type ProjectCatalog = { defaultDirectory: string; recent: ProjectSummary[] };
export type TranscriptPreview = { revision: string; source: string; segments: Array<{ start: number; end: number; text: string }> };
export type ProjectUpdate = Pick<CreatorProject, "directory" | "revision" | "name" | "recordings" | "editorial" | "outputDirectory">;

/** Ordered source contracts are the dependency edge for every generated result. */
export function projectSourceRevision(project: Pick<CreatorProject, "recordings">, recordingId: string | null): string {
  return JSON.stringify(project.recordings.filter((recording) => !recordingId || recording.id === recordingId));
}
