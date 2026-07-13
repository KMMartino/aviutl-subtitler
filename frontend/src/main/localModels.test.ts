import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import crypto from "node:crypto";
import { adoptExistingModelArtifacts, completeFilesNeedVerification, deleteManagedLocalProfile, fetchHuggingFaceFileMetadata, hasExpectedModelMetadata, LOCAL_PROFILES, localModelPaths, verifyDownloadedModelOrRemove } from "./localModels";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("local model catalog", () => {
  it("detects complete existing files without treating missing files as verifiable", () => {
    const root = makeTempRoot();
    const first = path.join(root, "first.gguf");
    const second = path.join(root, "second.gguf");
    fs.writeFileSync(first, "first");
    fs.writeFileSync(second, "second");
    const files = [
      { definition: { repo: "unsloth/gemma-4-E2B-it-GGUF", filename: "first.gguf", folder: "", bytes: 5 }, file: first },
      { definition: { repo: "unsloth/gemma-4-E2B-it-GGUF", filename: "second.gguf", folder: "", bytes: 6 }, file: second },
    ];
    expect(completeFilesNeedVerification(files)).toBe(true);
    fs.rmSync(second);
    expect(completeFilesNeedVerification(files)).toBe(false);
  });

  it("hash-verifies existing files and writes trusted sidecars without downloading", async () => {
    const root = makeTempRoot();
    const target = path.join(root, "model.gguf");
    const bytes = Buffer.from("existing model");
    fs.writeFileSync(target, bytes);
    const metadata = {
      revision: "739965d73654c0ead8020786aa998fc813070087",
      bytes: bytes.length,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
    };
    await adoptExistingModelArtifacts([
      { repo: "unsloth/gemma-4-E2B-it-GGUF", filename: "model.gguf", folder: "", bytes: bytes.length, target },
    ], () => undefined, async () => metadata);
    expect(hasExpectedModelMetadata(`${target}.artifact.json`, {
      source: "unsloth/gemma-4-E2B-it-GGUF/model.gguf",
      revision: metadata.revision,
      bytes: bytes.length,
    })).toBe(true);
  });

  it("rejects and removes a same-size corrupt existing file instead of adopting it", async () => {
    const root = makeTempRoot();
    const target = path.join(root, "model.gguf");
    fs.writeFileSync(target, "corrupt");
    const expected = Buffer.from("correct");
    await expect(adoptExistingModelArtifacts([
      { repo: "unsloth/gemma-4-E2B-it-GGUF", filename: "model.gguf", folder: "", bytes: expected.length, target },
    ], () => undefined, async () => ({
      revision: "739965d73654c0ead8020786aa998fc813070087",
      bytes: expected.length,
      sha256: crypto.createHash("sha256").update(expected).digest("hex"),
    }))).rejects.toThrow(/SHA-256 mismatch/);
    expect(fs.existsSync(target)).toBe(false);
    expect(fs.existsSync(`${target}.artifact.json`)).toBe(false);
  });

  it("accepts only immutable Hugging Face LFS metadata", async () => {
    const originalFetch = global.fetch;
    global.fetch = (async () => new Response(JSON.stringify({
      sha: "a".repeat(40),
      siblings: [{ rfilename: "model.gguf", size: 12, lfs: { sha256: "b".repeat(64) } }]
    }), { status: 200 })) as typeof fetch;
    try {
      await expect(fetchHuggingFaceFileMetadata("owner/repo", "model.gguf", "a".repeat(40))).resolves.toEqual({ revision: "a".repeat(40), bytes: 12, sha256: "b".repeat(64) });
      await expect(fetchHuggingFaceFileMetadata("owner/repo", "missing.gguf", "a".repeat(40))).rejects.toThrow(/did not provide/);
    } finally {
      global.fetch = originalFetch;
    }
  });

  it("requests a pinned revision and rejects a changed repository head", async () => {
    const originalFetch = global.fetch;
    const pinned = "a".repeat(40);
    let requestedUrl = "";
    global.fetch = (async (input) => {
      requestedUrl = String(input);
      return new Response(JSON.stringify({
        sha: "c".repeat(40),
        siblings: [{ rfilename: "model.gguf", size: 12, lfs: { sha256: "b".repeat(64) } }]
      }), { status: 200 });
    }) as typeof fetch;
    try {
      await expect(fetchHuggingFaceFileMetadata("owner/repo", "model.gguf", pinned)).rejects.toThrow(/did not provide/);
      expect(requestedUrl).toContain(`/revision/${pinned}?blobs=true`);
    } finally {
      global.fetch = originalFetch;
    }
  });

  it("rejects managed sidecars for another source or revision", () => {
    const root = makeTempRoot();
    const metadataPath = path.join(root, "model.artifact.json");
    const expected = { source: "owner/repo/model.gguf", revision: "a".repeat(40), bytes: 12 };
    fs.writeFileSync(metadataPath, JSON.stringify({ ...expected, sha256: "b".repeat(64) }));
    expect(hasExpectedModelMetadata(metadataPath, expected)).toBe(true);
    fs.writeFileSync(metadataPath, JSON.stringify({ ...expected, revision: "c".repeat(40), sha256: "b".repeat(64) }));
    expect(hasExpectedModelMetadata(metadataPath, expected)).toBe(false);
    fs.writeFileSync(metadataPath, JSON.stringify({ ...expected, source: "attacker/repo/model.gguf", sha256: "b".repeat(64) }));
    expect(hasExpectedModelMetadata(metadataPath, expected)).toBe(false);
  });

  it("removes a Hugging Face-mode target after failed verification", async () => {
    const root = makeTempRoot();
    const target = path.join(root, "model.gguf");
    fs.writeFileSync(target, "corrupt");
    fs.writeFileSync(`${target}.artifact.json`, "{}");
    const expected = Buffer.from("expected");
    await expect(verifyDownloadedModelOrRemove(target, {
      revision: "a".repeat(40),
      bytes: expected.length,
      sha256: crypto.createHash("sha256").update(expected).digest("hex"),
    })).rejects.toThrow();
    expect(fs.existsSync(target)).toBe(false);
    expect(fs.existsSync(`${target}.artifact.json`)).toBe(false);
  });
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
    expect(LOCAL_PROFILES.map((profile) => profile.cleanupGroupPolicy)).toEqual([
      { minSec: 20, durationDivisor: 8, maxSec: 180 },
      { minSec: 40, durationDivisor: 4, maxSec: 300 },
      { minSec: 60, durationDivisor: 2, maxSec: 600 },
      { minSec: 20, durationDivisor: 8, maxSec: 180 },
      { minSec: 40, durationDivisor: 4, maxSec: 300 },
      { minSec: 60, durationDivisor: 2, maxSec: 600 }
    ]);
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
