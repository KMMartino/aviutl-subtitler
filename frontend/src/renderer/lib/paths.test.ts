import { describe, expect, it } from "vitest";
import { defaultOutputPath, defaultSidecarDir } from "./paths";

describe("paths", () => {
  it("computes workflow output names", () => {
    expect(defaultOutputPath("C:\\media\\clip.mkv", "local")).toBe("C:\\media\\clip.exo");
    expect(defaultOutputPath("C:\\media\\clip.mkv", "hosted")).toBe("C:\\media\\clip-hosted-gemini35-gpt54mini.exo");
    expect(defaultOutputPath("C:\\media\\clip.mkv", "local-long-stream")).toBe("C:\\media\\clip-long-stream-local.exo");
    expect(defaultOutputPath("C:\\media\\clip.mkv", "hosted-long-stream")).toBe("C:\\media\\clip-long-stream-hosted.exo");
  });

  it("computes sidecar directory", () => {
    expect(defaultSidecarDir("C:\\media\\clip.mkv")).toBe("C:\\media\\subtitle_files");
  });
});
