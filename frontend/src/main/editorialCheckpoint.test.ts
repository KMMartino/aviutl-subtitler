import { describe, expect, it } from "vitest";
import { buildEditorialApplyCutsArgs, buildEditorialInspectionArgs } from "./editorialCheckpoint";

describe("editorial checkpoint inspection command", () => {
  it("passes the checkpoint and selected logical sources without shell interpolation", () => {
    const source = {
      path: "C:\\media\\run-game.mp4",
      durationSeconds: 60,
      mode: "paired" as const,
      audioPath: "C:\\media\\run-face.mp4",
      visualPath: "C:\\media\\run-game.mp4",
      audioDurationSeconds: 60,
      visualDurationSeconds: 60,
      width: 1920,
      height: 1080,
      audioWidth: 1280,
      audioHeight: 720,
      frameRate: 60,
      audioFrameRate: 60,
      pairingBasis: "filename" as const,
      roleConfirmed: true,
    };
    const args = buildEditorialInspectionArgs("C:\\media\\run-editorial.json", [source]);
    expect(args.slice(0, 5)).toEqual([
      "-m", "subtitler.editorial_project_cli", "inspect", "--checkpoint", "C:\\media\\run-editorial.json",
    ]);
    expect(JSON.parse(args[args.indexOf("--source-spec") + 1])).toMatchObject({
      audioPath: source.audioPath,
      visualPath: source.visualPath,
    });
  });
});

it("builds the reviewed-cut application command without shell interpolation", () => {
  const paths = {
    userConfigRoot: "C:\\state\\configs",
    envFile: "C:\\state\\.env",
    bundledBackendRoot: "C:\\app\\backend",
  } as never;
  expect(buildEditorialApplyCutsArgs(paths, "C:\\media\\run-editorial.exo")).toEqual([
    "-m", "subtitler.editorial_project_cli", "apply-cuts",
    "--review-project", "C:\\media\\run-editorial.exo",
    "--config", "C:\\state\\configs\\hosted-long-stream.json",
    "--env-file", "C:\\state\\.env",
    "--workspace", "C:\\media\\run-editorial.files\\narration-review",
    "--pipeline-script", "C:\\app\\backend\\aviutl_subtitle.py",
  ]);
});
