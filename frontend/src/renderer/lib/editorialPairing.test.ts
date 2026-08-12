import { describe, expect, it } from "vitest";
import type { MediaAnalysis } from "./types";
import { buildEditorialSources, setPairedAudioRole } from "./editorialPairing";

function media(durationSeconds: number, width: number, height: number, fps = 60): MediaAnalysis {
  return { durationSeconds, width, height, averageFrameRate: fps, nominalFrameRate: fps, frameRateMode: "reported-cfr", formatName: "mp4", videoCodec: "h264", thumbnailDataUrl: "", audioTracks: [] };
}

describe("editorial facecam/gameplay pairing", () => {
  it("pairs matching hyphen and dot stems from loose filename roles", () => {
    const sources = buildEditorialSources([
      { path: "C:\\run\\session-game-capture.mp4", analysis: media(60, 3840, 2160) },
      { path: "C:\\run\\session-face-camera.mp4", analysis: media(60.1, 1920, 1080) },
      { path: "C:\\run\\next.webcam.mp4", analysis: media(30, 1280, 720) },
      { path: "C:\\run\\next.screen.mp4", analysis: media(30, 1920, 1080) },
    ]);
    expect(sources).toHaveLength(2);
    expect(sources[0]).toMatchObject({ mode: "paired", audioPath: "C:\\run\\session-face-camera.mp4", visualPath: "C:\\run\\session-game-capture.mp4", pairingBasis: "filename", roleConfirmed: true });
    expect(sources[1]).toMatchObject({ mode: "paired", audioPath: "C:\\run\\next.webcam.mp4", visualPath: "C:\\run\\next.screen.mp4" });
  });

  it("falls back to resolution and prompts when equal resolutions remain ambiguous", () => {
    const resolved = buildEditorialSources([
      { path: "C:\\run\\part-left.mp4", analysis: media(60, 1280, 720) },
      { path: "C:\\run\\part-right.mp4", analysis: media(60, 1920, 1080) },
    ])[0];
    expect(resolved).toMatchObject({ mode: "paired", audioPath: "C:\\run\\part-left.mp4", visualPath: "C:\\run\\part-right.mp4", pairingBasis: "resolution", roleConfirmed: true });

    const ambiguous = buildEditorialSources([
      { path: "C:\\run\\same-one.mp4", analysis: media(60, 1920, 1080) },
      { path: "C:\\run\\same-two.mp4", analysis: media(60, 1920, 1080) },
    ])[0];
    expect(ambiguous).toMatchObject({ mode: "paired", pairingBasis: "manual", roleConfirmed: false });
    expect(setPairedAudioRole(ambiguous, "C:\\run\\same-two.mp4")).toMatchObject({ audioPath: "C:\\run\\same-two.mp4", visualPath: "C:\\run\\same-one.mp4", roleConfirmed: true });
  });

  it("uses the single-file path when nominal pairs differ by more than ten frames", () => {
    const sources = buildEditorialSources([
      { path: "C:\\run\\part-game.mp4", analysis: media(60, 1920, 1080, 60) },
      { path: "C:\\run\\part-face.mp4", analysis: media(60.2, 1280, 720, 60) },
    ]);
    expect(sources).toHaveLength(2);
    expect(sources.every((source) => source.mode === "single")).toBe(true);
  });
});
