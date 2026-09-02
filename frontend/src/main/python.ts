import path from "node:path";
import fs from "node:fs";
import type { RunRequest } from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";
import { managedFfmpegBinDir } from "./ffmpegManager";

export function defaultPythonPath(paths: RuntimePaths): string {
  const venvPython = path.join(paths.appResourceRoot, ".venv-win", "Scripts", "python.exe");
  return fs.existsSync(venvPython) ? venvPython : "";
}

export function buildRunCommand(paths: RuntimePaths, pythonPath: string, request: RunRequest): { command: string; args: string[]; preview: string; cwd: string; env: NodeJS.ProcessEnv } {
  const script = path.join(paths.bundledBackendRoot, "aviutl_subtitle.py");
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUTF8: "1" };
  const ffmpegBin = managedFfmpegBinDir(paths);
  if (ffmpegBin) {
    env.PATH = `${ffmpegBin}${path.delimiter}${env.PATH ?? ""}`;
  }
  const args = request.editorialProject || request.editorialCheckpoint ? buildEditorialArgs(paths, request) : [
    script,
    request.inputPath,
    "--workflow",
    request.workflow,
    "--config",
    request.configPath,
    "--env-file",
    request.envFile,
    "--output",
    request.outputPath,
    "--frontend-protocol",
    "stdio-v1",
    "--media-library-db",
    paths.mediaLibraryDatabase,
  ];
  if (request.editorialProject || request.editorialCheckpoint) {
    return {
      command: pythonPath,
      args,
      preview: [pythonPath, ...args].map(quoteArg).join(" "),
      cwd: paths.bundledBackendRoot,
      env,
    };
  }
  if (request.audioTrack !== undefined) {
    args.push("--audio-track", String(request.audioTrack));
  }
  if (request.sidecarDir) {
    args.push("--sidecar-dir", request.sidecarDir);
  }
  if (!request.sidecarsEnabled) {
    args.push("--no-sidecars");
  }
  if (fs.existsSync(paths.glossaryFile)) {
    args.push("--glossary", paths.glossaryFile);
  }
  if (request.profile) {
    args.push("--profile");
  }
  if (request.cutSilenceEncoderPreset !== "unconfigured") {
    args.push("--cut-silence-encoder", request.cutSilenceEncoderPreset);
  }
  return {
    command: pythonPath,
    args,
    preview: [pythonPath, ...args].map(quoteArg).join(" "),
    cwd: paths.bundledBackendRoot,
    env,
  };
}

function buildEditorialArgs(paths: RuntimePaths, request: RunRequest): string[] {
  if (request.editorialCheckpoint) {
    const args = [
      "-m", "subtitler.editorial_project_cli", "run",
      "--checkpoint", request.editorialCheckpoint,
      "--config", request.configPath,
      "--env-file", request.envFile,
      "--pipeline-script", path.join(paths.bundledBackendRoot, "aviutl_subtitle.py"),
      "--audio-track", String(request.audioTrack ?? 1),
      "--game-knowledge-store", path.join(paths.stateRoot, "editorial-game-knowledge.json"),
    ];
    for (const source of request.editorialCheckpointSources ?? []) args.push("--source-spec", JSON.stringify(source));
    if (request.editorialExtend && request.editorialProject) args.push("--extend-project-spec", JSON.stringify(request.editorialProject));
    if (request.sidecarDir) args.push("--workspace", request.sidecarDir);
    if (fs.existsSync(paths.glossaryFile)) args.push("--glossary", paths.glossaryFile);
    args.push("--restart-from", request.editorialRestartFrom ?? "compatible");
    return args;
  }
  const project = request.editorialProject!;
  const args = [
    "-m",
    "subtitler.editorial_project_cli",
    "start",
    "--checkpoint",
    request.outputPath,
    "--title",
    project.titleOrGame,
    "--objective",
    project.objective,
    "--target-min-sec",
    String(project.targetDurationMinSeconds),
    "--target-max-sec",
    String(project.targetDurationMaxSeconds),
    "--subtitle-mode",
    "full",
    "--output-locale",
    project.outputLocale ?? "en",
    "--config",
    request.configPath,
    "--env-file",
    request.envFile,
    "--pipeline-script",
    path.join(paths.bundledBackendRoot, "aviutl_subtitle.py"),
    "--audio-track",
    String(request.audioTrack ?? 1),
    "--game-knowledge-store",
    path.join(paths.stateRoot, "editorial-game-knowledge.json"),
  ];
  for (const source of project.sources) args.push("--source-spec", JSON.stringify(source));
  if (request.sidecarDir) args.push("--workspace", request.sidecarDir);
  if (fs.existsSync(paths.glossaryFile)) args.push("--glossary", paths.glossaryFile);
  return args;
}

function quoteArg(value: string): string {
  return /\s/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
}
