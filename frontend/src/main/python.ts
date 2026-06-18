import path from "node:path";
import fs from "node:fs";
import type { RunRequest } from "../renderer/lib/types";

export function projectRoot(): string {
  return path.resolve(__dirname, "..", "..", "..");
}

export function defaultPythonPath(root = projectRoot()): string {
  const venvPython = path.join(root, ".venv-win", "Scripts", "python.exe");
  return fs.existsSync(venvPython) ? venvPython : "python";
}

export function buildRunCommand(root: string, pythonPath: string, request: RunRequest): { command: string; args: string[]; preview: string } {
  const script = path.join(root, "aviutl_subtitle.py");
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
    request.outputPath
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
  if (request.profile) {
    args.push("--profile");
  }
  return {
    command: pythonPath,
    args,
    preview: [pythonPath, ...args].map(quoteArg).join(" ")
  };
}

function quoteArg(value: string): string {
  return /\s/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value;
}
