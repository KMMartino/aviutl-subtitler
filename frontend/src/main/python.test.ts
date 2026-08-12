import fs from "node:fs";
import os from "node:os";
import path from "node:path";
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
  mediaLibraryRoot: "C:/repo/.frontend-state/media-library",
  mediaLibraryDatabase: "C:/repo/.frontend-state/media-library/library.sqlite3",
  managedMediaRoot: "C:/Videos/SubUtl Media",
  managedWebMediaRoot: "C:/Videos/SubUtl Web Media",
  managedYtDlpRoot: "C:/state/tools/yt-dlp",
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

  it("passes the managed glossary when it exists", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-python-"));
    try {
      const glossaryFile = path.join(root, "userData", "glossary.txt");
      fs.mkdirSync(path.dirname(glossaryFile), { recursive: true });
      fs.writeFileSync(glossaryFile, "PSSR | PlayStation image upscaling\n", "utf8");
      const command = buildRunCommand({ ...paths, glossaryFile }, "python", {
        workflow: "hosted",
        inputPath: "C:/media/in.mkv",
        outputPath: "C:/media/in.exo",
        configPath: "C:/repo/config.json",
        envFile: "C:/repo/.env",
        profile: false,
        sidecarsEnabled: true
      });

      expect(command.args).toContain("--glossary");
      expect(command.args).toContain(glossaryFile);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("builds the serial editorial-project command without invoking the single-file CLI", () => {
    const command = buildRunCommand(paths, "python", {
      workflow: "hosted-long-stream",
      inputPath: "C:/media/part-1.mp4",
      outputPath: "C:/media/run.editorial.json",
      configPath: "C:/state/configs/hosted-long-stream.json",
      envFile: "C:/state/.env",
      audioTrack: 1,
      sidecarsEnabled: true,
      profile: true,
      cutSilenceEncoderPreset: "unconfigured",
      silencePreviewHeight: 360,
      silencePreviewFps: 8,
      editorialProject: {
        sources: [
          { path: "C:/media/part-1-game.mp4", durationSeconds: 3600, mode: "paired", audioPath: "C:/media/part-1-face.mp4", visualPath: "C:/media/part-1-game.mp4", audioDurationSeconds: 3600.1, visualDurationSeconds: 3600, width: 1920, height: 1080, audioWidth: 1280, audioHeight: 720, frameRate: 60, audioFrameRate: 60, pairingBasis: "filename", roleConfirmed: true },
          { path: "C:/media/part-2.mp4", durationSeconds: 1800, mode: "single", audioPath: "C:/media/part-2.mp4", visualPath: "C:/media/part-2.mp4", audioDurationSeconds: 1800, visualDurationSeconds: 1800, width: 1920, height: 1080, audioWidth: 1920, audioHeight: 1080, frameRate: 60, audioFrameRate: 60, pairingBasis: "single", roleConfirmed: true }
        ],
        titleOrGame: "Challenge run",
        objective: "Finish with the selected restriction",
        targetDurationMinSeconds: 2400,
        targetDurationMaxSeconds: 4200,
        mustKeepNotes: ["Final attempt"],
        deEmphasizeNotes: ["Repeated setup"],
        subtitleMode: "emphasis",
        outputLocale: "ja"
      }
    });

    expect(command.args.slice(0, 4)).toEqual(["-m", "subtitler.editorial_project_cli", "start", "--checkpoint"]);
    expect(command.args.filter((value) => value === "--source-spec")).toHaveLength(2);
    const firstSpec = JSON.parse(command.args[command.args.indexOf("--source-spec") + 1]);
    expect(firstSpec).toMatchObject({ mode: "paired", audioPath: "C:/media/part-1-face.mp4", visualPath: "C:/media/part-1-game.mp4" });
    expect(command.args).toContain("--must-keep");
    expect(command.args.slice(command.args.indexOf("--subtitle-mode"), command.args.indexOf("--subtitle-mode") + 2)).toEqual(["--subtitle-mode", "emphasis"]);
    expect(command.args.slice(command.args.indexOf("--output-locale"), command.args.indexOf("--output-locale") + 2)).toEqual(["--output-locale", "ja"]);
    expect(command.args).not.toContain("--workflow");
  });

  it("resumes an editorial checkpoint without recreating the project", () => {
    const command = buildRunCommand(paths, "python", {
      workflow: "hosted-long-stream",
      inputPath: "C:/media/run.editorial.json",
      outputPath: "C:/media/run.editorial.json",
      configPath: "C:/state/configs/hosted-long-stream.json",
      envFile: "C:/state/.env",
      sidecarsEnabled: true,
      profile: true,
      cutSilenceEncoderPreset: "unconfigured",
      silencePreviewHeight: 360,
      silencePreviewFps: 8,
      editorialCheckpoint: "C:/media/run.editorial.json"
    });
    expect(command.args.slice(0, 3)).toEqual(["-m", "subtitler.editorial_project_cli", "run"]);
    expect(command.args).not.toContain("--source");
    expect(command.args.slice(command.args.indexOf("--restart-from"), command.args.indexOf("--restart-from") + 2)).toEqual(["--restart-from", "compatible"]);
  });

  it("extends an editorial checkpoint with the full chronological project request", () => {
    const source = { path: "C:/media/part-1.mp4", durationSeconds: 60, mode: "single" as const, audioPath: "C:/media/part-1.mp4", visualPath: "C:/media/part-1.mp4", audioDurationSeconds: 60, visualDurationSeconds: 60, width: 1920, height: 1080, audioWidth: 1920, audioHeight: 1080, frameRate: 60, audioFrameRate: 60, pairingBasis: "single" as const, roleConfirmed: true };
    const project = { sources: [source], titleOrGame: "Recovered run", objective: "Continue it", targetDurationMinSeconds: 30, targetDurationMaxSeconds: 50, mustKeepNotes: [], deEmphasizeNotes: [] };
    const command = buildRunCommand(paths, "python", {
      workflow: "hosted-long-stream",
      inputPath: "C:/media/run.editorial.json",
      outputPath: "C:/media/run.editorial.json",
      configPath: "C:/state/configs/hosted-long-stream.json",
      envFile: "C:/state/.env",
      sidecarDir: "C:/media/run.files",
      sidecarsEnabled: true,
      profile: true,
      cutSilenceEncoderPreset: "unconfigured",
      silencePreviewHeight: 360,
      silencePreviewFps: 8,
      editorialCheckpoint: "C:/media/run.editorial.json",
      editorialCheckpointSources: [source],
      editorialProject: project,
      editorialExtend: true,
    });

    expect(JSON.parse(command.args[command.args.indexOf("--extend-project-spec") + 1])).toEqual(project);
    expect(command.args.slice(command.args.indexOf("--workspace"), command.args.indexOf("--workspace") + 2)).toEqual(["--workspace", "C:/media/run.files"]);
  });
});
