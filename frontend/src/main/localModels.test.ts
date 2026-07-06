import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { deleteManagedLocalProfile, LOCAL_PROFILES, localModelPaths } from "./localModels";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

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

  it("deletes only app-managed model profile files", () => {
    const root = makeTempRoot();
    const managed = path.join(root, "managed-models");
    const manual = path.join(root, "manual-models");
    const managedPaths = localModelPaths(managed, "8gb-gpu-gemma");
    const manualPaths = localModelPaths(manual, "8gb-gpu-gemma");
    for (const file of Object.values(managedPaths).filter(Boolean)) {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, "managed");
    }
    for (const file of Object.values(manualPaths).filter(Boolean)) {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, "manual");
    }

    const status = deleteManagedLocalProfile(managed, "8gb-gpu-gemma", managed);

    expect(status.installed).toBe(false);
    expect(Object.values(managedPaths).filter(Boolean).some((file) => fs.existsSync(file))).toBe(false);
    expect(Object.values(manualPaths).filter(Boolean).every((file) => fs.existsSync(file))).toBe(true);
  });

  it("refuses to delete local model files from a user-selected directory", () => {
    const root = makeTempRoot();
    const managed = path.join(root, "managed-models");
    const manual = path.join(root, "manual-models");
    const manualPaths = localModelPaths(manual, "8gb-gpu-gemma");
    for (const file of Object.values(manualPaths).filter(Boolean)) {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, "manual");
    }

    expect(() => deleteManagedLocalProfile(manual, "8gb-gpu-gemma", managed)).toThrow(/outside the app-managed models directory/);
    expect(Object.values(manualPaths).filter(Boolean).every((file) => fs.existsSync(file))).toBe(true);
  });
});

function makeTempRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-models-"));
  roots.push(root);
  return root;
}
