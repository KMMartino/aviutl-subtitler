import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import type { HuggingFaceDownloaderStatus, LocalModelProfile, LocalModelStatus, PythonRuntimeStatus } from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";
import { downloadVerifiedArtifact, verifyArtifact, writeArtifactMetadata } from "./artifactIntegrity";

type ModelFile = { repo: string; filename: string; sourcePath?: string; folder: string; bytes: number };
type HuggingFaceFileMetadata = { revision: string; bytes: number; sha256: string };
type ExistingModelArtifact = ModelFile & { target: string };
type ProfileDefinition = LocalModelProfile & {
  files: {
    transcription: ModelFile;
    projector: ModelFile;
    cleanup: ModelFile;
    transcriptionDraft?: ModelFile;
    cleanupDraft?: ModelFile;
  };
};

const e2bRepo = "unsloth/gemma-4-E2B-it-GGUF";
const e4bRepo = "unsloth/gemma-4-E4B-it-GGUF";
const b12Repo = "unsloth/gemma-4-12b-it-GGUF";
const PINNED_REVISIONS: Readonly<Record<string, string>> = {
  [e2bRepo]: "739965d73654c0ead8020786aa998fc813070087",
  [e4bRepo]: "0720adb23527c2cd5ea01d1db067cd960327fdac",
  [b12Repo]: "d997c805aafe035a8024f961c6e1afd6b30d79a5",
};

export const LOCAL_PROFILES: ProfileDefinition[] = [
  {
    id: "8gb-gpu-gemma",
    label: "8 GB GPU Profile (Gemma)",
    vramGb: 8,
    summary: "E2B Q5 transcription and E2B Q6 cleanup",
    downloadBytes: 3356035200 + 985654080 + 4501719168,
    cleanupGroupPolicy: { minSec: 20, durationDivisor: 8, maxSec: 180 },
    experimental: false,
    files: {
      transcription: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q5_K_M.gguf", folder: "gemma-4-e2b", bytes: 3356035200 },
      projector: { repo: e2bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e2b", bytes: 985654080 },
      cleanup: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q6_K.gguf", folder: "gemma-4-e2b", bytes: 4501719168 }
    }
  },
  {
    id: "12gb-gpu-gemma",
    label: "12 GB GPU Profile (Gemma)",
    vramGb: 12,
    summary: "E4B Q6 transcription and 12B Q5 cleanup",
    downloadBytes: 7074927776 + 990372672 + 8413574560,
    cleanupGroupPolicy: { minSec: 40, durationDivisor: 4, maxSec: 300 },
    experimental: false,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-Q5_K_M.gguf", folder: "gemma-4-12b", bytes: 8413574560 }
    }
  },
  {
    id: "16gb-gpu-gemma",
    label: "16 GB GPU Profile (Gemma)",
    vramGb: 16,
    summary: "E4B Q6 transcription and 12B Q6 cleanup",
    downloadBytes: 7074927776 + 990372672 + 10685011360,
    cleanupGroupPolicy: { minSec: 60, durationDivisor: 2, maxSec: 600 },
    experimental: false,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-UD-Q6_K_XL.gguf", folder: "gemma-4-12b", bytes: 10685011360 }
    }
  },
  {
    id: "8gb-gpu-gemma-mtp",
    label: "8 GB GPU Profile (Gemma MTP)",
    vramGb: 8,
    summary: "Experimental E2B profile with multi-token prediction",
    downloadBytes: 3356035200 + 985654080 + 4501719168 + 97817664,
    cleanupGroupPolicy: { minSec: 20, durationDivisor: 8, maxSec: 180 },
    experimental: true,
    files: {
      transcription: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q5_K_M.gguf", folder: "gemma-4-e2b", bytes: 3356035200 },
      projector: { repo: e2bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e2b", bytes: 985654080 },
      cleanup: { repo: e2bRepo, filename: "gemma-4-E2B-it-Q6_K.gguf", folder: "gemma-4-e2b", bytes: 4501719168 },
      transcriptionDraft: { repo: e2bRepo, sourcePath: "MTP/gemma-4-E2B-it-Q8_0-MTP.gguf", filename: "gemma-4-E2B-it-Q8_0-MTP.gguf", folder: "gemma-4-e2b/mtp", bytes: 97817664 },
      cleanupDraft: { repo: e2bRepo, sourcePath: "MTP/gemma-4-E2B-it-Q8_0-MTP.gguf", filename: "gemma-4-E2B-it-Q8_0-MTP.gguf", folder: "gemma-4-e2b/mtp", bytes: 97817664 }
    }
  },
  {
    id: "12gb-gpu-gemma-mtp",
    label: "12 GB GPU Profile (Gemma MTP)",
    vramGb: 12,
    summary: "Experimental E4B/12B profile with multi-token prediction",
    downloadBytes: 7074927776 + 990372672 + 8413574560 + 98653248 + 465109248,
    cleanupGroupPolicy: { minSec: 40, durationDivisor: 4, maxSec: 300 },
    experimental: true,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-Q5_K_M.gguf", folder: "gemma-4-12b", bytes: 8413574560 },
      transcriptionDraft: { repo: e4bRepo, sourcePath: "MTP/gemma-4-E4B-it-Q8_0-MTP.gguf", filename: "gemma-4-E4B-it-Q8_0-MTP.gguf", folder: "gemma-4-e4b/mtp", bytes: 98653248 },
      cleanupDraft: { repo: b12Repo, sourcePath: "MTP/gemma-4-12b-it-Q8_0-MTP.gguf", filename: "gemma-4-12b-it-Q8_0-MTP.gguf", folder: "gemma-4-12b/mtp", bytes: 465109248 }
    }
  },
  {
    id: "16gb-gpu-gemma-mtp",
    label: "16 GB GPU Profile (Gemma MTP)",
    vramGb: 16,
    summary: "Experimental E4B/12B profile with multi-token prediction",
    downloadBytes: 7074927776 + 990372672 + 10685011360 + 98653248 + 465109248,
    cleanupGroupPolicy: { minSec: 60, durationDivisor: 2, maxSec: 600 },
    experimental: true,
    files: {
      transcription: { repo: e4bRepo, filename: "gemma-4-E4B-it-Q6_K.gguf", folder: "gemma-4-e4b", bytes: 7074927776 },
      projector: { repo: e4bRepo, filename: "mmproj-F16.gguf", folder: "gemma-4-e4b", bytes: 990372672 },
      cleanup: { repo: b12Repo, filename: "gemma-4-12b-it-UD-Q6_K_XL.gguf", folder: "gemma-4-12b", bytes: 10685011360 },
      transcriptionDraft: { repo: e4bRepo, sourcePath: "MTP/gemma-4-E4B-it-Q8_0-MTP.gguf", filename: "gemma-4-E4B-it-Q8_0-MTP.gguf", folder: "gemma-4-e4b/mtp", bytes: 98653248 },
      cleanupDraft: { repo: b12Repo, sourcePath: "MTP/gemma-4-12b-it-Q8_0-MTP.gguf", filename: "gemma-4-12b-it-Q8_0-MTP.gguf", folder: "gemma-4-12b/mtp", bytes: 465109248 }
    }
  }
];

let downloading = false;

export type ModelDownloadMode = "direct" | "huggingface";

export function listLocalProfiles(): LocalModelProfile[] {
  return LOCAL_PROFILES.map(({ files: _files, ...profile }) => profile);
}

export function localModelStatus(modelsDirectory: string, profileId: string, managedModelsRoot = ""): LocalModelStatus {
  const profile = getProfile(profileId);
  const paths = localModelPaths(modelsDirectory, profileId);
  const requireMetadata = Boolean(managedModelsRoot) && samePath(modelsDirectory, managedModelsRoot);
  const catalogFiles = Object.entries(paths).filter(([, file]) => Boolean(file)) as Array<[keyof ProfileDefinition["files"], string]>;
  const installed = catalogFiles.every(([key, file]) => validCatalogFile(profile, key, file, requireMetadata));
  return {
    profile: profile.id,
    installed,
    needsVerification: requireMetadata && !installed && completeFilesNeedVerification(catalogFiles.map(([key, file]) => ({ definition: profile.files[key], file }))),
    downloading,
    managed: Boolean(managedModelsRoot) && samePath(modelsDirectory, managedModelsRoot),
    files: {
      transcription: { path: paths.transcription, exists: validCatalogFile(profile, "transcription", paths.transcription, requireMetadata) },
      projector: { path: paths.projector, exists: validCatalogFile(profile, "projector", paths.projector, requireMetadata) },
      cleanup: { path: paths.cleanup, exists: validCatalogFile(profile, "cleanup", paths.cleanup, requireMetadata) }
      ,
      ...(paths.transcriptionDraft ? { transcriptionDraft: { path: paths.transcriptionDraft, exists: validCatalogFile(profile, "transcriptionDraft", paths.transcriptionDraft, requireMetadata) } } : {}),
      ...(paths.cleanupDraft ? { cleanupDraft: { path: paths.cleanupDraft, exists: validCatalogFile(profile, "cleanupDraft", paths.cleanupDraft, requireMetadata) } } : {})
    }
  };
}

export function completeFilesNeedVerification(files: Array<{ definition: ModelFile | undefined; file: string }>): boolean {
  return files.length > 0 && files.every(({ definition, file }) => hasExpectedFileSize(definition, file));
}

export async function verifyExistingLocalProfile(
  modelsDirectory: string,
  profileId: string,
  managedModelsRoot: string,
  onLog: (text: string) => void = () => undefined,
): Promise<LocalModelStatus> {
  if (!samePath(modelsDirectory, managedModelsRoot)) {
    throw new Error("Existing-file verification is only available for app-managed models.");
  }
  const profile = getProfile(profileId);
  const paths = localModelPaths(modelsDirectory, profileId);
  const artifacts = Object.entries(profile.files).map(([key, file]) => ({
    ...file,
    target: paths[key as keyof typeof paths],
  }));
  if (!artifacts.every(({ target, ...file }) => hasExpectedFileSize(file, target))) {
    throw new Error("The model profile is incomplete. Download the missing files instead.");
  }
  await adoptExistingModelArtifacts(artifacts, onLog);
  return localModelStatus(modelsDirectory, profileId, managedModelsRoot);
}

export async function adoptExistingModelArtifacts(
  artifacts: ExistingModelArtifact[],
  onLog: (text: string) => void = () => undefined,
  metadataReader: typeof fetchHuggingFaceFileMetadata = fetchHuggingFaceFileMetadata,
): Promise<void> {
  for (const file of artifacts) {
    const source = file.sourcePath ?? file.filename;
    const metadata = await metadataReader(file.repo, source, pinnedRevisionFor(file.repo));
    if (metadata.bytes !== file.bytes) throw new Error(`Catalog size for ${file.filename} is stale: expected ${file.bytes}, Hugging Face reports ${metadata.bytes}.`);
    onLog(`[huggingface] verifying existing ${file.filename}\n`);
    await verifyDownloadedModelOrRemove(file.target, metadata);
    writeArtifactMetadata(`${file.target}.artifact.json`, {
      source: `${file.repo}/${source}`,
      ...metadata,
      installedAt: new Date().toISOString(),
    });
  }
  onLog("[huggingface] existing model profile verified\n");
}

export async function getHuggingFaceDownloaderStatus(paths: RuntimePaths, python?: PythonRuntimeStatus): Promise<HuggingFaceDownloaderStatus> {
  const resolvedPythonPath = python && python.source !== "missing" ? python.resolvedPath : "";
  const resolvedPythonSource = python && python.source !== "missing" ? python.source : "missing";
  const pythonPath = resolvedPythonPath || fallbackManagedPythonPath(paths);
  const pythonSource = resolvedPythonPath ? resolvedPythonSource : pythonPath ? "managed" : "missing";
  if (!pythonPath) {
    return {
      ready: false,
      pythonReady: false,
      pythonPath,
      pythonSource,
      version: "",
      xetReady: false,
      error: "Python is required for the faster Hugging Face downloader.",
    };
  }
  const script = [
    "import importlib.util, huggingface_hub",
    "xet = importlib.util.find_spec('hf_xet') is not None",
    "print(huggingface_hub.__version__ + '|' + str(xet))",
  ].join("; ");
  try {
    const output = await runCommand(pythonPath, ["-c", script]);
    const [version, xetReady] = output.trim().split("|");
    return { ready: true, pythonReady: true, pythonPath, pythonSource, version, xetReady: xetReady === "True", error: "" };
  } catch (error) {
    return {
      ready: false,
      pythonReady: true,
      pythonPath,
      pythonSource,
      version: "",
      xetReady: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function installHuggingFaceDownloader(paths: RuntimePaths, python?: PythonRuntimeStatus, onLog: (text: string) => void = () => undefined): Promise<HuggingFaceDownloaderStatus> {
  const pythonPath = python && python.source !== "missing" ? python.resolvedPath : fallbackManagedPythonPath(paths);
  if (!pythonPath) throw new Error("Python is required for the faster Hugging Face downloader. Create or select a Python runtime first.");
  onLog("$ python -m pip install --upgrade huggingface_hub[hf_xet] hf_xet\n");
  await runCommand(pythonPath, ["-m", "pip", "install", "--upgrade", "huggingface_hub[hf_xet]", "hf_xet"], onLog);
  return getHuggingFaceDownloaderStatus(paths, python);
}

export async function downloadLocalProfile(
  modelsDirectory: string,
  profileId: string,
  onLog: (text: string) => void = () => undefined,
  managedModelsRoot = "",
  mode: ModelDownloadMode = "direct",
  paths?: RuntimePaths,
  python?: PythonRuntimeStatus,
): Promise<LocalModelStatus> {
  if (downloading) throw new Error("A local model download is already running");
  downloading = true;
  try {
    const profile = getProfile(profileId);
    if (mode === "huggingface") {
      if (!paths) throw new Error("Runtime paths are required for Hugging Face downloads.");
      const status = await getHuggingFaceDownloaderStatus(paths, python);
      if (!status.ready) throw new Error(status.error || "Hugging Face downloader packages are not installed.");
      onLog(`[huggingface] using huggingface_hub ${status.version}${status.xetReady ? " with hf_xet" : ""}\n`);
    }
    for (const file of Object.values(profile.files)) {
      const target = path.join(modelsDirectory, file.folder, file.filename);
      const source = file.sourcePath ?? file.filename;
      const pinnedRevision = pinnedRevisionFor(file.repo);
      const metadata = await fetchHuggingFaceFileMetadata(file.repo, source, pinnedRevision);
      if (metadata.bytes !== file.bytes) throw new Error(`Catalog size for ${file.filename} is stale: expected ${file.bytes}, Hugging Face reports ${metadata.bytes}.`);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      const sourcePath = source.split("/").map(encodeURIComponent).join("/");
      if (mode === "huggingface" && paths) {
        if (fs.existsSync(target)) {
          try {
            await verifyArtifact(target, metadata);
            onLog(`[huggingface] verified cached ${file.filename}\n`);
          } catch {
            fs.rmSync(target, { force: true });
            fs.rmSync(`${target}.artifact.json`, { force: true });
          }
        }
        if (!fs.existsSync(target)) await downloadWithHuggingFaceHub(statusPythonPath(paths, python), file.repo, source, metadata.revision, path.dirname(target), target, onLog);
        await verifyDownloadedModelOrRemove(target, metadata);
      } else {
        const url = `https://huggingface.co/${file.repo}/resolve/${metadata.revision}/${sourcePath}?download=true`;
        onLog(`[huggingface] downloading ${file.repo}/${sourcePath}\n`);
        let lastPercent = -1;
        await downloadVerifiedArtifact(url, target, metadata, (received, total) => {
          const percent = Math.floor(received / total * 100);
          if (percent !== lastPercent && percent % 5 === 0) {
            lastPercent = percent;
            onLog(`[huggingface] ${file.filename}: ${formatBytes(received)} / ${formatBytes(total)} (${percent}%)\n`);
          }
        });
      }
      writeArtifactMetadata(`${target}.artifact.json`, { source: `${file.repo}/${source}`, ...metadata, installedAt: new Date().toISOString() });
      onLog(`[huggingface] saved ${target}\n`);
    }
    onLog("[huggingface] model profile download complete\n");
    return localModelStatus(modelsDirectory, profileId, managedModelsRoot);
  } finally {
    downloading = false;
  }
}

async function downloadWithHuggingFaceHub(
  pythonPath: string,
  repo: string,
  sourcePath: string,
  revision: string,
  localDir: string,
  target: string,
  onLog: (text: string) => void,
): Promise<void> {
  const script = [
    "import os, shutil, sys",
    "from huggingface_hub import hf_hub_download",
    "repo, filename, revision, local_dir, target = sys.argv[1:6]",
    "path = hf_hub_download(repo_id=repo, filename=filename, revision=revision, local_dir=local_dir)",
    "os.makedirs(os.path.dirname(target), exist_ok=True)",
    "if os.path.abspath(path) != os.path.abspath(target):",
    "    shutil.move(path, target)",
    "print(target)",
  ].join("\n");
  onLog(`[huggingface] downloading with Hugging Face downloader ${repo}/${sourcePath}\n`);
  await runCommand(pythonPath, ["-c", script, repo, sourcePath, revision, localDir, target], onLog, {
    HF_XET_HIGH_PERFORMANCE: "1",
  });
}

export function deleteManagedLocalProfile(modelsDirectory: string, profileId: string, managedModelsRoot: string): LocalModelStatus {
  if (!samePath(modelsDirectory, managedModelsRoot)) {
    throw new Error("Refusing to delete models outside the app-managed models directory.");
  }
  const root = path.resolve(managedModelsRoot);
  for (const file of Object.values(localModelPaths(modelsDirectory, profileId)).filter(Boolean)) {
    const target = path.resolve(file);
    if (!isWithinOrSame(root, target)) throw new Error(`Refusing to delete unmanaged model path: ${file}`);
    fs.rmSync(target, { force: true });
    fs.rmSync(`${target}.part`, { force: true });
    fs.rmSync(`${target}.artifact.json`, { force: true });
    pruneEmptyParents(path.dirname(target), root);
  }
  return localModelStatus(modelsDirectory, profileId, managedModelsRoot);
}

export function localModelPaths(modelsDirectory: string, profileId: string) {
  const profile = getProfile(profileId);
  const entry = (key: keyof ProfileDefinition["files"]) => {
    const file = profile.files[key];
    if (!file) return "";
    return path.join(modelsDirectory, file.folder, file.filename);
  };
  return {
    transcription: entry("transcription"),
    projector: entry("projector"),
    cleanup: entry("cleanup"),
    transcriptionDraft: profile.files.transcriptionDraft ? entry("transcriptionDraft") : "",
    cleanupDraft: profile.files.cleanupDraft ? entry("cleanupDraft") : ""
  };
}

function getProfile(profileId: string): ProfileDefinition {
  const profile = LOCAL_PROFILES.find((item) => item.id === profileId);
  if (!profile) throw new Error(`Unknown local model profile: ${profileId}`);
  return profile;
}

export async function fetchHuggingFaceFileMetadata(repo: string, filename: string, expectedRevision: string): Promise<HuggingFaceFileMetadata> {
  if (!/^[a-f0-9]{40}$/i.test(expectedRevision)) throw new Error(`Invalid pinned Hugging Face revision for ${repo}.`);
  const response = await fetch(`https://huggingface.co/api/models/${repo}/revision/${expectedRevision}?blobs=true`, { signal: AbortSignal.timeout(30_000) });
  if (!response.ok) throw new Error(`Could not read Hugging Face metadata for ${repo}: HTTP ${response.status}`);
  const info = await response.json() as {
    sha?: string;
    siblings?: Array<{ rfilename?: string; size?: number; lfs?: { sha256?: string } }>;
  };
  const sibling = info.siblings?.find((item) => item.rfilename === filename);
  const sha256 = sibling?.lfs?.sha256 ?? "";
  if (info.sha !== expectedRevision || !Number.isSafeInteger(sibling?.size) || (sibling?.size ?? 0) <= 0 || !/^[a-f0-9]{64}$/i.test(sha256)) {
    throw new Error(`Hugging Face did not provide immutable revision, size, and LFS SHA-256 metadata for ${repo}/${filename}.`);
  }
  return { revision: info.sha!, bytes: sibling!.size!, sha256 };
}

export async function verifyDownloadedModelOrRemove(target: string, metadata: HuggingFaceFileMetadata): Promise<void> {
  try {
    await verifyArtifact(target, metadata);
  } catch (error) {
    fs.rmSync(target, { force: true });
    fs.rmSync(`${target}.artifact.json`, { force: true });
    throw error;
  }
}

export function hasExpectedModelMetadata(
  metadataPath: string,
  expected: { source: string; revision: string; bytes: number },
): boolean {
  try {
    const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8")) as { source?: string; bytes?: number; sha256?: string; revision?: string };
    return metadata.source === expected.source
      && metadata.bytes === expected.bytes
      && metadata.revision === expected.revision
      && /^[a-f0-9]{64}$/i.test(metadata.sha256 ?? "");
  } catch {
    return false;
  }
}

function pinnedRevisionFor(repo: string): string {
  const revision = PINNED_REVISIONS[repo];
  if (!revision) throw new Error(`No immutable revision is pinned for ${repo}.`);
  return revision;
}

function validCatalogFile(profile: ProfileDefinition, key: keyof ProfileDefinition["files"], file: string, requireMetadata: boolean): boolean {
  const definition = profile.files[key];
  if (!definition || !hasExpectedFileSize(definition, file)) return false;
  if (!requireMetadata) return true;
  try {
    return hasExpectedModelMetadata(`${file}.artifact.json`, {
      source: `${definition.repo}/${definition.sourcePath ?? definition.filename}`,
      revision: pinnedRevisionFor(definition.repo),
      bytes: definition.bytes,
    });
  } catch {
    return false;
  }
}

function hasExpectedFileSize(definition: ModelFile | undefined, file: string): boolean {
  return Boolean(definition && file && fs.existsSync(file) && fs.statSync(file).size === definition.bytes);
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "?";
  const gib = bytes / 1024 / 1024 / 1024;
  if (gib >= 1) return `${gib.toFixed(2)} GiB`;
  return `${(bytes / 1024 / 1024).toFixed(0)} MiB`;
}

function managedPythonPath(paths: RuntimePaths): string {
  return path.join(paths.managedPythonRoot, ".venv", "Scripts", "python.exe");
}

function fallbackManagedPythonPath(paths: RuntimePaths): string {
  const pythonPath = managedPythonPath(paths);
  return fs.existsSync(pythonPath) ? pythonPath : "";
}

function statusPythonPath(paths: RuntimePaths, python?: PythonRuntimeStatus): string {
  return python && python.source !== "missing" ? python.resolvedPath : managedPythonPath(paths);
}

function runCommand(command: string, args: string[], onLog: (text: string) => void = () => undefined, extraEnv: NodeJS.ProcessEnv = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, env: { ...process.env, ...extraEnv } });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      stdout += text;
      onLog(text);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      stderr += text;
      onLog(text);
    });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve(stdout || stderr) : reject(new Error(stderr.trim() || `${command} failed`)));
  });
}

function samePath(left: string, right: string): boolean {
  return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase();
}

function isWithinOrSame(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function pruneEmptyParents(start: string, stopAt: string): void {
  let current = path.resolve(start);
  const stop = path.resolve(stopAt);
  while (isWithinOrSame(stop, current) && current !== stop) {
    try {
      fs.rmdirSync(current);
    } catch {
      return;
    }
    current = path.dirname(current);
  }
}
