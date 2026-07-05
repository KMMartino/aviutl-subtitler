import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { ensureFrontendState, loadAppState, saveWorkflowConfig } from "./configStore";
import type { RuntimePaths } from "./paths";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("config store runtime paths", () => {
  it("copies bundled config templates into writable user config state", () => {
    const paths = makePaths();
    fs.mkdirSync(paths.bundledConfigRoot, { recursive: true });
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "local.json"), JSON.stringify({ backend: { name: "existing-pipeline" } }));
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "hosted.json"), JSON.stringify({ hosted: true }));
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "local-long-stream.json"), JSON.stringify({ long: "local" }));
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "hosted-long-stream.json"), JSON.stringify({ long: "hosted" }));

    ensureFrontendState(paths);

    expect(fs.existsSync(path.join(paths.userConfigRoot, "local.json"))).toBe(true);
    expect(fs.existsSync(path.join(paths.stateRoot, "settings.json"))).toBe(true);
    expect(fs.existsSync(paths.envFile)).toBe(true);
  });

  it("does not overwrite existing user workflow configs", () => {
    const paths = makePaths();
    fs.mkdirSync(paths.bundledConfigRoot, { recursive: true });
    for (const workflow of ["local", "hosted", "local-long-stream", "hosted-long-stream"]) {
      fs.writeFileSync(path.join(paths.bundledConfigRoot, `${workflow}.json`), JSON.stringify({ template: workflow }));
    }
    ensureFrontendState(paths);
    saveWorkflowConfig("hosted", { user: "edited" }, paths);
    ensureFrontendState(paths);

    expect(loadAppState(paths).configs.hosted).toEqual({ user: "edited" });
  });
});

function makePaths(): RuntimePaths {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-config-"));
  roots.push(root);
  return {
    appResourceRoot: path.join(root, "resources"),
    bundledBackendRoot: path.join(root, "resources", "app-backend"),
    bundledConfigRoot: path.join(root, "resources", "app-backend", "configs"),
    userDataRoot: path.join(root, "userData"),
    stateRoot: path.join(root, "userData"),
    userConfigRoot: path.join(root, "userData", "configs"),
    userToolsRoot: path.join(root, "userData", "tools"),
    userModelsRoot: path.join(root, "userData", "models"),
    managedPythonRoot: path.join(root, "userData", "python"),
    managedFfmpegRoot: path.join(root, "userData", "tools", "ffmpeg"),
    envFile: path.join(root, "userData", ".env"),
    glossaryFile: path.join(root, "userData", "glossary.txt"),
  };
}
