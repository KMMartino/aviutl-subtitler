import { describe, expect, it } from "vitest";
import { validateIpcArguments } from "./ipcSecurity";

const request = {
  workflow: "hosted-long-stream", inputPath: "C:/media/video.mp4", outputPath: "C:/media/result.json",
  configPath: "C:/config/hosted-long-stream.json", envFile: "C:/config/.env", profile: true, sidecarsEnabled: true,
  cutSilenceEncoderPreset: "unconfigured", silencePreviewHeight: 360, silencePreviewFps: 8,
  moments: { sourcePath: "C:/media/video.mp4", facecamPath: "C:/media/face.mp4", speechSource: "facecam", query: "all boss fights",
    startSec: 15, endSec: 90, originOffsetSec: 0, locale: "en" },
};

describe("moment extraction requests", () => {
  it("accepts a bounded paired recording without enabling the editorial runner", () => {
    expect(() => validateIpcArguments("run:start", [request])).not.toThrow();
  });
  it("rejects invalid ranges, empty queries, relative paths and mixed workflow requests", () => {
    for (const changed of [{ startSec: -1 }, { endSec: 10 }, { startSec: Number.NaN }, { query: " " }, { facecamPath: "../face.mp4" }]) {
      expect(() => validateIpcArguments("run:start", [{ ...request, moments: { ...request.moments, ...changed } }])).toThrow();
    }
    expect(() => validateIpcArguments("run:start", [{ ...request, editorialCheckpoint: "C:/old.json" }])).toThrow();
  });
  it("validates optional URL download ranges", () => {
    expect(() => validateIpcArguments("source:acquire", ["https://example.com/video", undefined, "C:/project/Sources"])).not.toThrow();
    expect(() => validateIpcArguments("source:acquire", ["https://example.com/video", undefined, "../outside"])).toThrow();
    expect(() => validateIpcArguments("project:delete", ["C:/project"])).not.toThrow();
    expect(() => validateIpcArguments("project:delete", ["../project"])).toThrow();
    expect(() => validateIpcArguments("source:acquire", ["https://example.com/video", { startSec: 20, endSec: 40 }])).not.toThrow();
    expect(() => validateIpcArguments("source:acquire", ["https://example.com/video", { startSec: 40, endSec: 20 }])).toThrow();
    expect(() => validateIpcArguments("source:acquire", ["https://example.com/video", { startSec: 0, arbitraryFlag: true }])).toThrow();
  });
});
