import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import type { EditorialCheckpointInspection, EditorialCutApplicationResult, EditorialSourceSelection } from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";

export function buildEditorialInspectionArgs(checkpoint: string, sources?: EditorialSourceSelection[]): string[] {
  const args = ["-m", "subtitler.editorial_project_cli", "inspect", "--checkpoint", checkpoint];
  for (const source of sources ?? []) args.push("--source-spec", JSON.stringify(source));
  return args;
}

export function buildEditorialApplyCutsArgs(paths: RuntimePaths, reviewProject: string): string[] {
  const stem = path.basename(reviewProject, path.extname(reviewProject));
  return [
    "-m", "subtitler.editorial_project_cli", "apply-cuts",
    "--review-project", reviewProject,
    "--config", path.join(paths.userConfigRoot, "hosted-long-stream.json"),
    "--env-file", paths.envFile,
    "--workspace", path.join(path.dirname(reviewProject), `${stem}.files`, "narration-review"),
    "--pipeline-script", path.join(paths.bundledBackendRoot, "aviutl_subtitle.py"),
  ];
}

export async function applyReviewedEditorialCuts(
  paths: RuntimePaths,
  pythonPath: string,
  reviewProject: string,
  onOutput?: (stream: "stdout" | "stderr", text: string) => void,
): Promise<EditorialCutApplicationResult> {
  const args = buildEditorialApplyCutsArgs(paths, reviewProject);
  const { stdout, stderr, code } = await collectProcess(
    pythonPath, args, paths.bundledBackendRoot, onOutput,
  );
  if (code !== 0) {
    throw new Error(stderr.trim() || stdout.trim() || `Cut application exited with code ${code}.`);
  }
  try {
    const value = JSON.parse(stdout) as Record<string, unknown>;
    return {
      reviewProject: String(value.review_project ?? ""),
      checkpoint: String(value.checkpoint ?? ""),
      outputPath: String(value.output_path ?? ""),
      reportPath: String(value.report_path ?? ""),
      cutCount: Number(value.cut_count ?? 0),
      removedMs: Number(value.removed_ms ?? 0),
      ignoredShortCount: Number(value.ignored_short_count ?? 0),
      narrationBriefCount: Number(value.narration_brief_count ?? 0),
      narrationReferenceCount: Number(value.narration_reference_count ?? 0),
      apiCostUsd: Number(value.api_cost_usd ?? 0),
    };
  } catch {
    throw new Error("Cut application returned an unreadable response.");
  }
}

export async function inspectEditorialCheckpoint(
  paths: RuntimePaths,
  pythonPath: string,
  checkpoint: string,
  sources?: EditorialSourceSelection[]
): Promise<EditorialCheckpointInspection> {
  const args = buildEditorialInspectionArgs(checkpoint, sources);
  const { stdout, stderr, code } = await collectProcess(pythonPath, args, paths.bundledBackendRoot);
  if (code !== 0) throw new Error(stderr.trim() || stdout.trim() || `Checkpoint inspection exited with code ${code}.`);
  try {
    return JSON.parse(stdout) as EditorialCheckpointInspection;
  } catch {
    throw new Error("Checkpoint inspection returned an unreadable response.");
  }
}

export async function findMatchingEditorialCheckpoint(
  paths: RuntimePaths,
  pythonPath: string,
  sources: EditorialSourceSelection[]
): Promise<{ path: string; inspection: EditorialCheckpointInspection } | null> {
  const candidates = checkpointCandidates(sources);
  let best: { path: string; inspection: EditorialCheckpointInspection } | null = null;
  for (const checkpoint of candidates) {
    try {
      const inspection = await inspectEditorialCheckpoint(paths, pythonPath, checkpoint, sources);
      if (!inspection.matches_sources) continue;
      if (!best || inspection.match_kind === "full" || inspection.matched_source_count > best.inspection.matched_source_count) {
        best = { path: checkpoint, inspection };
      }
      if (inspection.match_kind === "full") break;
    } catch {
      // Unrelated or stale JSON files are not recovery candidates.
    }
  }
  return best;
}

function checkpointCandidates(sources: EditorialSourceSelection[]): string[] {
  const candidates = new Set<string>();
  for (const source of sources) {
    const directory = path.dirname(source.visualPath);
    const stem = path.basename(source.visualPath, path.extname(source.visualPath));
    candidates.add(path.join(directory, `${stem}-editorial.json`));
    try {
      for (const name of fs.readdirSync(directory)) {
        if (name.toLocaleLowerCase().endsWith("editorial.json")) candidates.add(path.join(directory, name));
      }
    } catch {
      // The media analyzer will report inaccessible source directories separately.
    }
  }
  return [...candidates].filter((candidate) => fs.existsSync(candidate)).slice(0, 50);
}

function collectProcess(
  command: string,
  args: string[],
  cwd: string,
  onOutput?: (stream: "stdout" | "stderr", text: string) => void,
): Promise<{ stdout: string; stderr: string; code: number | null }> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      windowsHide: true,
      env: { ...process.env, PYTHONUTF8: "1" },
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    child.stdout.on("data", (chunk: Buffer) => {
      stdout.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr.push(chunk);
      onOutput?.("stderr", chunk.toString("utf8"));
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({
      stdout: Buffer.concat(stdout).toString("utf8"),
      stderr: Buffer.concat(stderr).toString("utf8"),
      code,
    }));
  });
}
