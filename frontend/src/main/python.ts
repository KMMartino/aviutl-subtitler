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
  const args = [
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
    "stdio-v1"
  ];
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

function quoteArg(value: string): string {
  return /\s/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
}
