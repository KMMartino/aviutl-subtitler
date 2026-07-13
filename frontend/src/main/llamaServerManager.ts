import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import extract from "extract-zip";
import type { CurrentLlamaServerState, LlamaBackendId, LlamaBackendOption, LlamaReleaseAsset, LlamaReleaseCheck, ManagedLlamaStatus } from "../renderer/lib/types";
import { downloadVerifiedArtifact, writeArtifactMetadata } from "./artifactIntegrity";

type GithubAsset = {
  name: string;
  browser_download_url: string;
  size: number;
  digest: string | null;
};

type GithubRelease = {
  tag_name: string;
  assets: GithubAsset[];
};

export const LLAMA_BACKENDS = [
  {
    id: "vulkan",
    label: "Vulkan",
    description: "Recommended for AMD on Windows; also broadly compatible on NVIDIA and Intel."
  },
  {
    id: "cuda-12",
    label: "CUDA 12.4",
    description: "Recommended NVIDIA option for this app; requires compatible NVIDIA drivers."
  }
] as const satisfies readonly LlamaBackendOption[];

const latestReleaseUrl = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest";

export function listLlamaBackends(): LlamaBackendOption[] {
  return LLAMA_BACKENDS.map((backend) => ({ ...backend }));
}

export function assetPattern(backend: LlamaBackendId): RegExp {
  switch (backend) {
    case "vulkan":
      return /^llama-.+-bin-win-vulkan-x64\.zip$/;
    case "cuda-12":
      return /^llama-.+-bin-win-cuda-12\.4-x64\.zip$/;
  }
}

export function matchReleaseAsset(release: GithubRelease, backend: LlamaBackendId): LlamaReleaseAsset {
  const asset = release.assets.find((item) => assetPattern(backend).test(item.name));
  if (!asset) {
    const windowsAssets = release.assets
      .filter((item) => item.name.includes("-bin-win-"))
      .map((item) => `- ${item.name}`)
      .join("\n");
    const label = backend === "vulkan" ? "Vulkan" : "CUDA 12.4";
    throw new Error(`No llama.cpp Windows ${label} asset found in release ${release.tag_name}.\nAvailable Windows assets:\n${windowsAssets || "- none"}`);
  }
  const sha256 = asset.digest?.match(/^sha256:([a-f0-9]{64})$/i)?.[1] ?? "";
  if (!sha256 || !Number.isSafeInteger(asset.size) || asset.size <= 0) {
    throw new Error(`llama.cpp release ${release.tag_name} does not provide trustworthy SHA-256 and size metadata for ${asset.name}.`);
  }
  return {
    backend,
    releaseTag: release.tag_name,
    assetName: asset.name,
    downloadUrl: asset.browser_download_url,
    bytes: asset.size,
    sha256,
  };
}

export async function checkLatestLlamaRelease(): Promise<LlamaReleaseCheck> {
  const release = await fetchLatestRelease();
  return {
    releaseTag: release.tag_name,
    assets: LLAMA_BACKENDS.map((backend) => matchReleaseAsset(release, backend.id)),
    checkedAt: new Date().toISOString()
  };
}

export function managedLlamaRoot(userToolsRoot: string): string {
  return path.join(userToolsRoot, "llama");
}

/** Adopt installs written by the old stateRoot/.frontend-state/tools contract. */
export function migrateLegacyManagedLlamaRoot(stateRoot: string, userToolsRoot: string): boolean {
  const legacy = path.join(stateRoot, ".frontend-state", "tools", "llama");
  const target = managedLlamaRoot(userToolsRoot);
  if (!fs.existsSync(legacy)) return false;
  let moved: boolean;
  if (!fs.existsSync(target)) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.renameSync(legacy, target);
    moved = true;
  } else {
    moved = moveMissingTree(legacy, target);
  }
  removeEmptyParents(path.dirname(legacy), stateRoot);
  return moved;
}

export function managedLlamaInstallDir(root: string, backend: LlamaBackendId, releaseTag: string): string {
  return path.join(managedLlamaRoot(root), backend, releaseTag);
}

export function getManagedLlamaStatus(root: string, backend: LlamaBackendId, releaseTag?: string): ManagedLlamaStatus {
  const tag = releaseTag || latestInstalledRelease(root, backend);
  const installDir = tag ? managedLlamaInstallDir(root, backend, tag) : path.join(managedLlamaRoot(root), backend);
  const serverPath = tag && hasInstallMetadata(installDir, tag) ? findLlamaServerExe(installDir) : "";
  return {
    backend,
    releaseTag: tag,
    installed: Boolean(serverPath),
    installDir,
    serverPath,
    version: serverPath ? versionOfServerSync(serverPath) : ""
  };
}

export function getCurrentLlamaServerState(root: string, serverPath: string): CurrentLlamaServerState {
  const valid = Boolean(serverPath && fs.existsSync(serverPath));
  const managed = valid && isManagedServerPath(root, serverPath);
  if (!managed) {
    return {
      managed: false,
      valid,
      backend: "",
      releaseTag: "",
      serverPath,
      version: valid ? versionOfServerSync(serverPath) : "",
      previous: null
    };
  }
  const parsed = parseManagedServerPath(root, serverPath);
  const previous = parsed ? previousInstalledStatus(root, parsed.backend, parsed.releaseTag) : null;
  return {
    managed: true,
    valid,
    backend: parsed?.backend ?? "",
    releaseTag: parsed?.releaseTag ?? "",
    serverPath,
    version: versionOfServerSync(serverPath),
    previous
  };
}

export function deleteManagedLlamaBackend(root: string, backend: LlamaBackendId): ManagedLlamaStatus {
  const backendRoot = path.join(managedLlamaRoot(root), backend);
  if (!isWithin(managedLlamaRoot(root), backendRoot)) throw new Error("Refusing to delete llama-server outside the managed app tools directory.");
  fs.rmSync(backendRoot, { recursive: true, force: true });
  return getManagedLlamaStatus(root, backend);
}

export async function downloadManagedLlamaServer(
  root: string,
  backend: LlamaBackendId,
  onLog: (text: string) => void = () => undefined
): Promise<ManagedLlamaStatus> {
  onLog("$ llama.cpp server download\n");
  onLog("[llama] checking latest release\n");
  const release = await fetchLatestRelease();
  onLog(`[llama] latest release: ${release.tag_name}\n`);
  const asset = matchReleaseAsset(release, backend);
  onLog(`[llama] selected asset: ${asset.assetName}\n`);

  const existing = getManagedLlamaStatus(root, backend, release.tag_name);
  if (existing.installed) {
    onLog(`[llama] already installed: ${existing.serverPath}\n`);
    return existing;
  }

  const downloadsDir = path.join(managedLlamaRoot(root), "downloads");
  const zipPath = path.join(downloadsDir, asset.assetName);
  fs.mkdirSync(downloadsDir, { recursive: true });

  onLog(`[llama] acquiring and verifying ${asset.downloadUrl}\n`);
  try {
    let lastPercent = -1;
    await downloadVerifiedArtifact(asset.downloadUrl, zipPath, asset, (received, total) => {
      const percent = Math.floor(received / total * 100);
      if (percent !== lastPercent && percent % 5 === 0) {
        lastPercent = percent;
        onLog(`[llama] download ${percent}%\n`);
      }
    });
  } catch (error) {
    throw new Error(`Could not download verified llama.cpp server: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
  }
  onLog(`[llama] verified SHA-256 ${asset.sha256}\n`);

  const installDir = managedLlamaInstallDir(root, backend, release.tag_name);
  const stagingDir = `${installDir}.part`;
  fs.rmSync(stagingDir, { recursive: true, force: true });
  fs.mkdirSync(stagingDir, { recursive: true });
  onLog(`[llama] extracting to staging directory\n`);
  await extract(zipPath, { dir: stagingDir });
  fs.rmSync(installDir, { recursive: true, force: true });
  fs.renameSync(stagingDir, installDir);

  const serverPath = findLlamaServerExe(installDir);
  if (!serverPath) {
    throw new Error(`Downloaded llama.cpp ${release.tag_name}, but llama-server.exe was not found in extracted files.`);
  }
  onLog(`[llama] found ${serverPath}\n`);

  let version = "";
  try {
    version = await versionOfServer(serverPath);
    onLog(`[llama] version: ${firstLine(version)}\n`);
  } catch (error) {
    onLog(`[llama] installed, but version check failed: ${error instanceof Error ? error.message : String(error)}\n`);
  }
  onLog("[llama] install complete\n");
  writeArtifactMetadata(path.join(installDir, "artifact.json"), {
    source: asset.downloadUrl,
    bytes: asset.bytes,
    sha256: asset.sha256,
    revision: release.tag_name,
    installedAt: new Date().toISOString(),
  });
  pruneOldManagedInstalls(root, backend, release.tag_name, onLog);

  return {
    backend,
    releaseTag: release.tag_name,
    installed: true,
    installDir,
    serverPath,
    version
  };
}

export function pruneOldManagedInstalls(root: string, backend: LlamaBackendId, currentReleaseTag: string, onLog: (text: string) => void = () => undefined): void {
  const backendRoot = path.join(managedLlamaRoot(root), backend);
  if (!fs.existsSync(backendRoot)) return;
  const releases = installedReleases(root, backend);
  const current = releases.find((release) => release.name === currentReleaseTag);
  const previous = releases.find((release) => release.name !== currentReleaseTag);
  const keep = new Set([current?.name, previous?.name].filter(Boolean));
  for (const release of releases) {
    if (keep.has(release.name)) continue;
    fs.rmSync(path.join(backendRoot, release.name), { recursive: true, force: true });
    onLog(`[llama] removed old install ${backend}/${release.name}\n`);
  }
}

export function findLlamaServerExe(directory: string): string {
  if (!directory || !fs.existsSync(directory)) return "";
  const stack = [directory];
  while (stack.length) {
    const current = stack.pop() ?? "";
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
      } else if (entry.isFile() && entry.name.toLowerCase() === "llama-server.exe") {
        return fullPath;
      }
    }
  }
  return "";
}

async function fetchLatestRelease(): Promise<GithubRelease> {
  const response = await fetch(latestReleaseUrl, {
    headers: { "Accept": "application/vnd.github+json" },
    signal: AbortSignal.timeout(30_000)
  });
  if (!response.ok) throw new Error(`Could not check llama.cpp latest release: HTTP ${response.status}`);
  return await response.json() as GithubRelease;
}

function latestInstalledRelease(root: string, backend: LlamaBackendId): string {
  return installedReleases(root, backend)[0]?.name ?? "";
}

function installedReleases(root: string, backend: LlamaBackendId): Array<{ name: string; mtime: number }> {
  const backendRoot = path.join(managedLlamaRoot(root), backend);
  if (!fs.existsSync(backendRoot)) return [];
  return fs.readdirSync(backendRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const fullPath = path.join(backendRoot, entry.name);
      return { name: entry.name, mtime: fs.statSync(fullPath).mtimeMs };
    })
    .sort((a, b) => b.mtime - a.mtime || b.name.localeCompare(a.name));
}

function previousInstalledStatus(root: string, backend: LlamaBackendId, currentReleaseTag: string): ManagedLlamaStatus | null {
  const previous = installedReleases(root, backend).find((release) => release.name !== currentReleaseTag);
  if (!previous) return null;
  const status = getManagedLlamaStatus(root, backend, previous.name);
  return status.installed ? status : null;
}

function isManagedServerPath(root: string, serverPath: string): boolean {
  const relative = path.relative(managedLlamaRoot(root), serverPath);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function isWithin(root: string, target: string): boolean {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function parseManagedServerPath(root: string, serverPath: string): { backend: LlamaBackendId; releaseTag: string } | null {
  const relative = path.relative(managedLlamaRoot(root), serverPath);
  const [backend, releaseTag] = relative.split(path.sep);
  if ((backend === "vulkan" || backend === "cuda-12") && releaseTag) {
    return { backend, releaseTag };
  }
  return null;
}

function versionOfServerSync(serverPath: string): string {
  const result = spawnSync(serverPath, ["--version"], { encoding: "utf8", timeout: 5000, windowsHide: true });
  if (result.error || result.status !== 0) return "";
  return `${result.stdout}${result.stderr}`.trim();
}

function hasInstallMetadata(installDir: string, releaseTag: string): boolean {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(installDir, "artifact.json"), "utf8")) as { bytes?: number; sha256?: string; revision?: string };
    return Number.isSafeInteger(value.bytes) && (value.bytes ?? 0) > 0 && /^[a-f0-9]{64}$/i.test(value.sha256 ?? "") && value.revision === releaseTag;
  } catch {
    return false;
  }
}

function versionOfServer(serverPath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(serverPath, ["--version"], { windowsHide: true });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk.toString(); });
    child.stderr.on("data", (chunk) => { output += chunk.toString(); });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve(output.trim());
      else reject(new Error(`llama-server --version exited with code ${code}`));
    });
  });
}

function firstLine(text: string): string {
  return text.split(/\r?\n/).find(Boolean) ?? "";
}

function removeEmptyParents(start: string, stop: string): void {
  let current = path.resolve(start);
  const boundary = path.resolve(stop);
  while (current !== boundary && isWithin(boundary, current)) {
    try {
      if (fs.readdirSync(current).length) return;
      fs.rmdirSync(current);
    } catch {
      return;
    }
    current = path.dirname(current);
  }
}

function moveMissingTree(source: string, target: string): boolean {
  let moved = false;
  fs.mkdirSync(target, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const from = path.join(source, entry.name);
    const to = path.join(target, entry.name);
    if (!fs.existsSync(to)) {
      fs.renameSync(from, to);
      moved = true;
    } else if (entry.isDirectory() && fs.statSync(to).isDirectory()) {
      moved = moveMissingTree(from, to) || moved;
      if (fs.existsSync(from) && fs.readdirSync(from).length === 0) fs.rmdirSync(from);
    }
  }
  if (fs.existsSync(source) && fs.readdirSync(source).length === 0) fs.rmdirSync(source);
  return moved;
}
