import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { pipeline } from "node:stream/promises";
import { Readable, Transform } from "node:stream";
import type { HuggingFaceDownloaderStatus, LocalModelProfile, LocalModelStatus, PythonRuntimeStatus } from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";

type ModelFile = { repo: string; filename: string; sourcePath?: string; folder: string; bytes: number };
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

export const LOCAL_PROFILES: ProfileDefinition[] = [
  {
    id: "8gb-gpu-gemma",
    label: "8 GB GPU Profile (Gemma)",
    vramGb: 8,
    summary: "E2B Q5 transcription and E2B Q6 cleanup",
    downloadBytes: 3356035200 + 985654080 + 4501719168,
    cleanupWindowSubtitles: 64,
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
    cleanupWindowSubtitles: 128,
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
    cleanupWindowSubtitles: 256,
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
    cleanupWindowSubtitles: 64,
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
    cleanupWindowSubtitles: 128,
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
    cleanupWindowSubtitles: 256,
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
  return {
    profile: profile.id,
    installed: Object.values(paths).filter(Boolean).every((file) => fs.existsSync(file)),
    downloading,
    managed: Boolean(managedModelsRoot) && samePath(modelsDirectory, managedModelsRoot),
    files: {
      transcription: { path: paths.transcription, exists: fs.existsSync(paths.transcription) },
      projector: { path: paths.projector, exists: fs.existsSync(paths.projector) },
      cleanup: { path: paths.cleanup, exists: fs.existsSync(paths.cleanup) }
      ,
      ...(paths.transcriptionDraft ? { transcriptionDraft: { path: paths.transcriptionDraft, exists: fs.existsSync(paths.transcriptionDraft) } } : {}),
      ...(paths.cleanupDraft ? { cleanupDraft: { path: paths.cleanupDraft, exists: fs.existsSync(paths.cleanupDraft) } } : {})
    }
  };
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
      if (fs.existsSync(target)) {
        onLog(`[huggingface] exists, skipping ${file.filename}\n`);
        continue;
      }
      fs.mkdirSync(path.dirname(target), { recursive: true });
      const sourcePath = (file.sourcePath ?? file.filename).split("/").map(encodeURIComponent).join("/");
      if (mode === "huggingface" && paths) {
        await downloadWithHuggingFaceHub(statusPythonPath(paths, python), file.repo, file.sourcePath ?? file.filename, path.dirname(target), target, onLog);
      } else {
        const temporary = `${target}.part`;
        const url = `https://huggingface.co/${file.repo}/resolve/main/${sourcePath}?download=true`;
        onLog(`[huggingface] downloading ${file.repo}/${sourcePath}\n`);
        const response = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(4 * 60 * 60 * 1000) });
        if (!response.ok || !response.body) throw new Error(`Download failed for ${file.filename}: HTTP ${response.status}`);
        await pipeline(
          Readable.fromWeb(response.body as never),
          progressLogger(file.filename, Number(response.headers.get("content-length")) || file.bytes, onLog),
          fs.createWriteStream(temporary)
        );
        fs.renameSync(temporary, target);
      }
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
  localDir: string,
  target: string,
  onLog: (text: string) => void,
): Promise<void> {
  const script = [
    "import os, shutil, sys",
    "from huggingface_hub import hf_hub_download",
    "repo, filename, local_dir, target = sys.argv[1:5]",
    "path = hf_hub_download(repo_id=repo, filename=filename, local_dir=local_dir)",
    "os.makedirs(os.path.dirname(target), exist_ok=True)",
    "if os.path.abspath(path) != os.path.abspath(target):",
    "    shutil.move(path, target)",
    "print(target)",
  ].join("\n");
  onLog(`[huggingface] downloading with Hugging Face downloader ${repo}/${sourcePath}\n`);
  await runCommand(pythonPath, ["-c", script, repo, sourcePath, localDir, target], onLog, {
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

function progressLogger(filename: string, totalBytes: number, onLog: (text: string) => void): Transform {
  let downloaded = 0;
  let lastPercent = -1;
  let lastLog = 0;
  return new Transform({
    transform(chunk: Buffer, _encoding, callback) {
      downloaded += chunk.length;
      const now = Date.now();
      const percent = totalBytes > 0 ? Math.floor((downloaded / totalBytes) * 100) : 0;
      if ((percent !== lastPercent && percent % 5 === 0) || now - lastLog > 10_000) {
        lastPercent = percent;
        lastLog = now;
        onLog(`[huggingface] ${filename}: ${formatBytes(downloaded)} / ${formatBytes(totalBytes)} (${percent}%)\n`);
      }
      callback(null, chunk);
    }
  });
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
