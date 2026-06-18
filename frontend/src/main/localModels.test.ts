import path from "node:path";
import { describe, expect, it } from "vitest";
import { LOCAL_PROFILES, localModelPaths } from "./localModels";

describe("local model catalog", () => {
  it("defines the fixed 16 GB profile", () => {
    expect(LOCAL_PROFILES.map((profile) => profile.id)).toEqual([
      "8gb-gpu-gemma",
      "12gb-gpu-gemma",
      "16gb-gpu-gemma",
      "8gb-gpu-gemma-mtp",
      "12gb-gpu-gemma-mtp",
      "16gb-gpu-gemma-mtp"
    ]);
    expect(LOCAL_PROFILES[0].files.transcription.filename).toBe("gemma-4-E2B-it-Q5_K_M.gguf");
    expect(LOCAL_PROFILES[1].files.cleanup.filename).toBe("gemma-4-12b-it-Q5_K_M.gguf");
    expect(LOCAL_PROFILES[2].files.cleanup.filename).toBe("gemma-4-12b-it-UD-Q6_K_XL.gguf");
    expect(LOCAL_PROFILES.map((profile) => profile.cleanupWindowSubtitles)).toEqual([10, 20, 32, 10, 20, 32]);
  });

  it("reuses standard target paths in MTP profiles", () => {
    const root = path.resolve("C:/models");
    const standard = localModelPaths(root, "12gb-gpu-gemma");
    const mtp = localModelPaths(root, "12gb-gpu-gemma-mtp");
    expect(mtp.transcription).toBe(standard.transcription);
    expect(mtp.projector).toBe(standard.projector);
    expect(mtp.cleanup).toBe(standard.cleanup);
    expect(mtp.transcriptionDraft).toContain("MTP");
    expect(mtp.cleanupDraft).toContain("MTP");
  });

  it("resolves all files under the managed directory", () => {
    const root = path.resolve("C:/models");
    for (const file of Object.values(localModelPaths(root, "8gb-gpu-gemma")).filter(Boolean)) {
      expect(file.startsWith(root)).toBe(true);
    }
  });
});
