import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { downloadVerifiedArtifact, writeArtifactMetadata } from "./artifactIntegrity";
import { runtimePaths } from "./paths";

export type YtDlpStatus = {
  ready: boolean;
  executablePath: string;
  version: string;
  managedInstalled: boolean;
  channel: "nightly";
  error: string;
};

type GithubReleaseAsset = {
  name?: string;
  size?: number;
  digest?: string;
  browser_download_url?: string;
};

type GithubRelease = {
  tag_name?: string;
  assets?: GithubReleaseAsset[];
};

export async function getYtDlpStatus(paths = runtimePaths()): Promise<YtDlpStatus> {
  const executablePath = path.join(paths.managedYtDlpRoot, "yt-dlp.exe");
  if (!fs.existsSync(executablePath)) {
    return {
      ready: false,
      executablePath: "",
      version: "",
      managedInstalled: false,
      channel: "nightly",
      error: "Managed yt-dlp is not installed.",
    };
  }
  try {
    const version = (await run(executablePath, ["--version"], 15_000)).trim().split(/\r?\n/u)[0] ?? "";
    return {
      ready: true,
      executablePath,
      version,
      managedInstalled: true,
      channel: "nightly",
      error: "",
    };
  } catch (error) {
    return {
      ready: false,
      executablePath,
      version: "",
      managedInstalled: true,
      channel: "nightly",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function installOrUpdateYtDlp(
  onLog: (text: string) => void = () => undefined,
  paths = runtimePaths(),
): Promise<YtDlpStatus> {
  onLog("$ yt-dlp managed nightly update\n");
  const response = await fetch(
    "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest",
    {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "SubUtl",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      signal: AbortSignal.timeout(60_000),
    },
  );
  if (!response.ok) throw new Error(`Could not check the latest yt-dlp nightly release (HTTP ${response.status}).`);
  const release = await response.json() as GithubRelease;
  const asset = release.assets?.find((candidate) => candidate.name === "yt-dlp.exe");
  let sha256 = asset?.digest?.replace(/^sha256:/iu, "") ?? "";
  if (!/^[a-f0-9]{64}$/iu.test(sha256)) {
    const checksums = release.assets?.find((candidate) => candidate.name === "SHA2-256SUMS");
    if (checksums?.browser_download_url) {
      const checksumResponse = await fetch(checksums.browser_download_url, {
        signal: AbortSignal.timeout(60_000),
      });
      if (checksumResponse.ok) {
        const line = (await checksumResponse.text()).split(/\r?\n/u)
          .find((candidate) => /\syt-dlp\.exe$/iu.test(candidate.trim()));
        sha256 = line?.trim().split(/\s+/u)[0] ?? "";
      }
    }
  }
  if (
    !asset?.browser_download_url
    || !Number.isSafeInteger(asset.size)
    || Number(asset.size) <= 0
    || !/^[a-f0-9]{64}$/iu.test(sha256)
  ) {
    throw new Error("The latest yt-dlp release did not provide a verifiable Windows executable.");
  }
  const destination = path.join(paths.managedYtDlpRoot, "yt-dlp.exe");
  const staged = path.join(paths.managedYtDlpRoot, "yt-dlp.next.exe");
  const backup = path.join(paths.managedYtDlpRoot, "yt-dlp.previous.exe");
  onLog(`[yt-dlp] downloading verified nightly ${release.tag_name ?? ""}\n`);
  await downloadVerifiedArtifact(
    asset.browser_download_url,
    staged,
    { bytes: Number(asset.size), sha256 },
  );
  fs.rmSync(backup, { force: true });
  if (fs.existsSync(destination)) fs.renameSync(destination, backup);
  try {
    fs.renameSync(staged, destination);
    fs.rmSync(backup, { force: true });
  } catch (error) {
    if (fs.existsSync(backup) && !fs.existsSync(destination)) fs.renameSync(backup, destination);
    throw error;
  }
  writeArtifactMetadata(path.join(paths.managedYtDlpRoot, "artifact.json"), {
    source: asset.browser_download_url,
    bytes: Number(asset.size),
    sha256,
    revision: release.tag_name,
    installedAt: new Date().toISOString(),
  });
  const status = await getYtDlpStatus(paths);
  if (!status.ready) throw new Error(status.error || "yt-dlp was downloaded but could not be started.");
  onLog(`[yt-dlp] ready: ${status.version}\n`);
  return status;
}

export async function deleteManagedYtDlp(paths = runtimePaths()): Promise<YtDlpStatus> {
  const expected = path.resolve(paths.userToolsRoot);
  const target = path.resolve(paths.managedYtDlpRoot);
  if (!target.startsWith(`${expected}${path.sep}`)) {
    throw new Error("Refusing to delete yt-dlp outside the managed app tools directory.");
  }
  fs.rmSync(target, { recursive: true, force: true });
  return getYtDlpStatus(paths);
}

function run(command: string, args: string[], timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      action();
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(() => reject(new Error("yt-dlp version check timed out.")));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString("utf8"); });
    child.on("error", (error) => finish(() => reject(error)));
    child.on("close", (code) => finish(() => {
      if (code === 0) resolve(stdout);
      else reject(new Error(stderr.trim() || `yt-dlp exited with code ${code}.`));
    }));
  });
}
