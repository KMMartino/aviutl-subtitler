import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  findLlamaServerExe,
  getManagedLlamaStatus,
  getCurrentLlamaServerState,
  deleteManagedLlamaBackend,
  listLlamaBackends,
  managedLlamaInstallDir,
  matchReleaseAsset,
  pruneOldManagedInstalls
} from "./llamaServerManager";

const tempRoots: string[] = [];

afterEach(() => {
  for (const root of tempRoots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("llama server manager", () => {
  it("selects the Vulkan Windows x64 asset", () => {
    const asset = matchReleaseAsset(fakeRelease(["llama-b9670-bin-win-vulkan-x64.zip"]), "vulkan");
    expect(asset).toMatchObject({
      backend: "vulkan",
      releaseTag: "b9670",
      assetName: "llama-b9670-bin-win-vulkan-x64.zip"
    });
  });

  it("selects the CUDA 12.4 Windows x64 asset", () => {
    const asset = matchReleaseAsset(fakeRelease(["llama-b9670-bin-win-cuda-12.4-x64.zip"]), "cuda-12");
    expect(asset).toMatchObject({
      backend: "cuda-12",
      releaseTag: "b9670",
      assetName: "llama-b9670-bin-win-cuda-12.4-x64.zip"
    });
  });

  it("includes available Windows assets in missing asset errors", () => {
    expect(() => matchReleaseAsset(fakeRelease(["llama-b9670-bin-win-cuda-13.0-x64.zip"]), "vulkan"))
      .toThrow(/Available Windows assets:\n- llama-b9670-bin-win-cuda-13\.0-x64\.zip/);
  });

  it("resolves managed installs under frontend state", () => {
    const root = path.resolve("C:/repo/subtitler");
    expect(managedLlamaInstallDir(root, "vulkan", "b9670")).toBe(path.join(root, ".frontend-state", "tools", "llama", "vulkan", "b9670"));
  });

  it("finds llama-server.exe recursively", () => {
    const root = makeTempRoot();
    const nested = path.join(root, "build", "bin");
    fs.mkdirSync(nested, { recursive: true });
    const server = path.join(nested, "llama-server.exe");
    fs.writeFileSync(server, "");
    expect(findLlamaServerExe(root)).toBe(server);
  });

  it("reports not installed when executable is missing", () => {
    const root = makeTempRoot();
    const status = getManagedLlamaStatus(root, "vulkan", "b9670");
    expect(status).toMatchObject({
      backend: "vulkan",
      releaseTag: "b9670",
      installed: false,
      serverPath: ""
    });
  });

  it("exposes exactly Vulkan and CUDA 12.4", () => {
    expect(listLlamaBackends().map((backend) => backend.id)).toEqual(["vulkan", "cuda-12"]);
  });

  it("detects current managed server state and previous install", () => {
    const root = makeTempRoot();
    const previous = createManagedServer(root, "vulkan", "b9669");
    const current = createManagedServer(root, "vulkan", "b9670");
    fs.utimesSync(previous, new Date(Date.now() - 20_000), new Date(Date.now() - 20_000));

    const state = getCurrentLlamaServerState(root, current);

    expect(state.managed).toBe(true);
    expect(state.backend).toBe("vulkan");
    expect(state.releaseTag).toBe("b9670");
    expect(state.previous?.releaseTag).toBe("b9669");
    expect(state.previous?.serverPath).toBe(previous);
  });

  it("reports manual server paths as valid but unmanaged", () => {
    const root = makeTempRoot();
    const manual = path.join(root, "manual", "llama-server.exe");
    fs.mkdirSync(path.dirname(manual), { recursive: true });
    fs.writeFileSync(manual, "");

    const state = getCurrentLlamaServerState(root, manual);

    expect(state.valid).toBe(true);
    expect(state.managed).toBe(false);
    expect(state.previous).toBeNull();
  });

  it("keeps current and previous installs while pruning older releases", () => {
    const root = makeTempRoot();
    createManagedServer(root, "vulkan", "b9668");
    createManagedServer(root, "vulkan", "b9669");
    createManagedServer(root, "vulkan", "b9670");

    pruneOldManagedInstalls(root, "vulkan", "b9670");

    expect(fs.existsSync(managedLlamaInstallDir(root, "vulkan", "b9670"))).toBe(true);
    expect(fs.existsSync(managedLlamaInstallDir(root, "vulkan", "b9669"))).toBe(true);
    expect(fs.existsSync(managedLlamaInstallDir(root, "vulkan", "b9668"))).toBe(false);
  });

  it("deletes only the selected managed backend installs", () => {
    const root = makeTempRoot();
    createManagedServer(root, "vulkan", "b9670");
    const cuda = createManagedServer(root, "cuda-12", "b9670");

    const status = deleteManagedLlamaBackend(root, "vulkan");

    expect(status.installed).toBe(false);
    expect(fs.existsSync(managedLlamaInstallDir(root, "vulkan", "b9670"))).toBe(false);
    expect(fs.existsSync(cuda)).toBe(true);
  });
});

function fakeRelease(assetNames: string[]) {
  return {
    tag_name: "b9670",
    assets: assetNames.map((name) => ({
      name,
      browser_download_url: `https://example.test/${name}`
    }))
  };
}

function makeTempRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-llama-"));
  tempRoots.push(root);
  return root;
}

function createManagedServer(root: string, backend: "vulkan" | "cuda-12", releaseTag: string): string {
  const directory = path.join(managedLlamaInstallDir(root, backend, releaseTag), "bin");
  fs.mkdirSync(directory, { recursive: true });
  const server = path.join(directory, "llama-server.exe");
  fs.writeFileSync(server, "");
  return server;
}
