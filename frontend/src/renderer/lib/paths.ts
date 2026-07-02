import type { WorkflowName } from "./types";

export function normalizeSlashes(path: string): string {
  return path.replace(/\\/g, "/");
}

export function dirname(path: string): string {
  const normalized = normalizeSlashes(path);
  const index = normalized.lastIndexOf("/");
  if (index < 0) return "";
  return path.slice(0, index);
}

export function basenameWithoutExt(path: string): string {
  const normalized = normalizeSlashes(path);
  const slash = normalized.lastIndexOf("/");
  const file = slash >= 0 ? normalized.slice(slash + 1) : normalized;
  const dot = file.lastIndexOf(".");
  return dot > 0 ? file.slice(0, dot) : file;
}

export function joinPath(dir: string, name: string): string {
  if (!dir) return name;
  const separator = dir.includes("\\") ? "\\" : "/";
  return `${dir.replace(/[\\/]$/, "")}${separator}${name}`;
}

export function defaultOutputPath(inputPath: string, workflow: WorkflowName): string {
  if (!inputPath) return "";
  const dir = dirname(inputPath);
  const stem = basenameWithoutExt(inputPath);
  const suffix: Record<WorkflowName, string> = {
    local: "",
    hosted: "-hosted",
    "local-long-stream": "-long-stream-local",
    "hosted-long-stream": "-long-stream-hosted"
  };
  return joinPath(dir, `${stem}${suffix[workflow]}.exo`);
}

export function defaultSidecarDir(inputPath: string): string {
  if (!inputPath) return "";
  return joinPath(dirname(inputPath), "subtitle_files");
}
