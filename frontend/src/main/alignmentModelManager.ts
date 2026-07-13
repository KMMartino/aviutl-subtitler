import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import type { AlignmentModelStatus, PythonRuntimeStatus } from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";
import { sha256File, writeArtifactMetadata } from "./artifactIntegrity";

export const ALIGNMENT_MODEL = {
  repo: "MahmoudAshraf/mms-300m-1130-forced-aligner",
  revision: "49402e9577b1158620820667c218cd494cc44486",
  downloadBytes: 1_261_934_082,
  modelFile: "model.safetensors",
  modelBytes: 1_261_930_388,
  modelSha256: "9aa5229a1af4d7714285cfa14bcaab6af2a39a9e810b475722b24975d5cb1dab",
  files: {
    "config.json": 2076,
    "model.safetensors": 1_261_930_388,
    "preprocessor_config.json": 211,
    "special_tokens_map.json": 74,
    "tokenizer_config.json": 1047,
    "vocab.json": 286,
  },
} as const;

export function managedAlignmentModelDir(paths: RuntimePaths): string {
  return path.join(paths.userModelsRoot, "alignment", ALIGNMENT_MODEL.revision);
}

export async function getAlignmentModelStatus(paths: RuntimePaths): Promise<AlignmentModelStatus> {
  const cachePath = managedAlignmentModelDir(paths);
  return inspectAlignmentModel(cachePath, ALIGNMENT_MODEL);
}

export async function inspectAlignmentModel(
  cachePath: string,
  definition: { revision: string; downloadBytes: number; modelFile: string; modelSha256: string; files: Readonly<Record<string, number>> },
): Promise<AlignmentModelStatus> {
  let error = "";
  for (const [name, bytes] of Object.entries(definition.files)) {
    const file = path.join(cachePath, name);
    if (!fs.existsSync(file) || fs.statSync(file).size !== bytes) {
      return { installed: false, modelPath: "", cachePath, revision: definition.revision, downloadBytes: definition.downloadBytes, verified: false, error: `${name} is missing or has the wrong size.` };
    }
  }
  const digest = await sha256File(path.join(cachePath, definition.modelFile));
  if (digest !== definition.modelSha256) error = `${definition.modelFile} failed SHA-256 verification.`;
  return {
    installed: !error,
    modelPath: error ? "" : cachePath,
    cachePath,
    revision: definition.revision,
    downloadBytes: definition.downloadBytes,
    verified: !error,
    error,
  };
}

export async function downloadAlignmentModel(
  paths: RuntimePaths,
  python: PythonRuntimeStatus,
  onLog: (text: string) => void = () => undefined,
): Promise<AlignmentModelStatus> {
  if (!python.ready || !python.requirementsInstalled) throw new Error("Install the Python requirements before downloading the alignment model.");
  const existing = await getAlignmentModelStatus(paths);
  if (existing.installed) return existing;
  const target = managedAlignmentModelDir(paths);
  const staging = `${target}.part`;
  fs.rmSync(staging, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(target), { recursive: true });
  onLog(`[alignment] downloading ${ALIGNMENT_MODEL.repo}@${ALIGNMENT_MODEL.revision}\n`);
  const script = [
    "import sys",
    "from huggingface_hub import snapshot_download",
    "snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3], allow_patterns=sys.argv[4:])",
  ].join("\n");
  try {
    await runCommand(python.resolvedPath, ["-c", script, ALIGNMENT_MODEL.repo, ALIGNMENT_MODEL.revision, staging, ...Object.keys(ALIGNMENT_MODEL.files)], onLog);
    fs.rmSync(target, { recursive: true, force: true });
    fs.renameSync(staging, target);
    const status = await verifyPromotedAlignmentDirectory(target, () => getAlignmentModelStatus(paths));
    writeArtifactMetadata(path.join(target, "artifact.json"), {
      source: ALIGNMENT_MODEL.repo,
      revision: ALIGNMENT_MODEL.revision,
      bytes: ALIGNMENT_MODEL.modelBytes,
      sha256: ALIGNMENT_MODEL.modelSha256,
      installedAt: new Date().toISOString(),
    });
    onLog(`[alignment] verified SHA-256 ${ALIGNMENT_MODEL.modelSha256}\n`);
    return status;
  } catch (error) {
    fs.rmSync(staging, { recursive: true, force: true });
    throw new Error(`Could not install the alignment model: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
  }
}

export async function verifyPromotedAlignmentDirectory(
  target: string,
  inspect: () => Promise<AlignmentModelStatus>,
): Promise<AlignmentModelStatus> {
  try {
    const status = await inspect();
    if (!status.installed) throw new Error(status.error || "Alignment model verification failed.");
    return status;
  } catch (error) {
    fs.rmSync(target, { recursive: true, force: true });
    throw error;
  }
}

export async function deleteAlignmentModel(paths: RuntimePaths): Promise<AlignmentModelStatus> {
  const alignmentRoot = path.join(paths.userModelsRoot, "alignment");
  const relative = path.relative(paths.userModelsRoot, alignmentRoot);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Refusing to delete outside the managed models directory.");
  fs.rmSync(alignmentRoot, { recursive: true, force: true });
  return getAlignmentModelStatus(paths);
}

function runCommand(command: string, args: string[], onLog: (text: string) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, env: { ...process.env, HF_XET_HIGH_PERFORMANCE: "1" } });
    child.stdout.on("data", (chunk: Buffer) => onLog(chunk.toString("utf8")));
    child.stderr.on("data", (chunk: Buffer) => onLog(chunk.toString("utf8")));
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`Hugging Face downloader exited with code ${code}.`)));
  });
}
