import { describe, expect, it } from "vitest";
import { buildRunCommand } from "./python";
import type { RuntimePaths } from "./paths";

const paths: RuntimePaths = {
  appResourceRoot: "C:/repo",
  bundledBackendRoot: "C:/repo",
  bundledConfigRoot: "C:/repo/configs",
  userDataRoot: "C:/repo/.frontend-state",
  stateRoot: "C:/repo/.frontend-state",
  userConfigRoot: "C:/repo/.frontend-state/configs",
  userToolsRoot: "C:/repo/.frontend-state/tools",
  userModelsRoot: "C:/repo/.frontend-state/models",
  managedPythonRoot: "C:/repo/.frontend-state/python",
  managedFfmpegRoot: "C:/repo/.frontend-state/tools/ffmpeg",
  envFile: "C:/repo/.env",
  glossaryFile: "C:/repo/glossary.txt",
};

describe("python command builder", () => {
  it("emits only supported CLI flags", () => {
    const command = buildRunCommand(paths, "python", {
      workflow: "local",
      inputPath: "C:/media/in.mkv",
      outputPath: "C:/media/in.exo",
      configPath: "C:/repo/.frontend-state/configs/local.json",
      envFile: "C:/repo/.env",
      audioTrack: 0,
      sidecarDir: "C:/media/subtitle_files",
      sidecarsEnabled: true,
      profile: true
    });

    expect(command.args).toContain("--workflow");
    expect(command.args).toContain("--config");
    expect(command.args).toContain("--env-file");
    expect(command.args).toContain("--output");
    expect(command.args).toContain("--audio-track");
    expect(command.args).toContain("--sidecar-dir");
    expect(command.args).toContain("--profile");
    expect(command.args).not.toContain("--model");
    expect(command.args).not.toContain("--cleanup-model");
  });

  it("disables sidecars explicitly", () => {
    const command = buildRunCommand(paths, "python", {
      workflow: "hosted",
      inputPath: "C:/media/in.mkv",
      outputPath: "C:/media/in.exo",
      configPath: "C:/repo/config.json",
      envFile: "C:/repo/.env",
      profile: false,
      sidecarsEnabled: false
    });
    expect(command.args).toContain("--no-sidecars");
    expect(command.args).not.toContain("--sidecar-dir");
  });

  it("runs from the bundled backend root", () => {
    const command = buildRunCommand({ ...paths, bundledBackendRoot: "C:/app/resources/app-backend" }, "python", {
      workflow: "hosted",
      inputPath: "C:/media/in.mkv",
      outputPath: "C:/media/in.exo",
      configPath: "C:/state/configs/hosted.json",
      envFile: "C:/state/.env",
      profile: false,
      sidecarsEnabled: true
    });
    expect(command.cwd).toBe("C:/app/resources/app-backend");
    expect(command.args[0]).toBe("C:\\app\\resources\\app-backend\\aviutl_subtitle.py");
    expect(command.env.PYTHONUTF8).toBe("1");
  });
});
