import { describe, expect, it } from "vitest";
import { buildRunCommand } from "./python";

describe("python command builder", () => {
  it("emits only supported CLI flags", () => {
    const command = buildRunCommand("C:/repo", "python", {
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
    const command = buildRunCommand("C:/repo", "python", {
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
});
