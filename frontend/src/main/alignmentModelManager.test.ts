import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { inspectAlignmentModel, verifyPromotedAlignmentDirectory } from "./alignmentModelManager";

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("managed alignment model", () => {
  it("is ready only when every pinned file and model digest verifies", async () => {
    const root = tempRoot();
    const model = Buffer.from("tiny model");
    fs.writeFileSync(path.join(root, "model.bin"), model);
    fs.writeFileSync(path.join(root, "config.json"), "{}\n");
    const definition = {
      revision: "a".repeat(40), downloadBytes: model.length + 3, modelFile: "model.bin",
      modelSha256: crypto.createHash("sha256").update(model).digest("hex"),
      files: { "model.bin": model.length, "config.json": 3 },
    };
    await expect(inspectAlignmentModel(root, definition)).resolves.toMatchObject({ installed: true, verified: true, modelPath: root });
    fs.writeFileSync(path.join(root, "model.bin"), Buffer.alloc(model.length, 7));
    await expect(inspectAlignmentModel(root, definition)).resolves.toMatchObject({ installed: false, verified: false, modelPath: "" });
  });

  it("reports a missing required file", async () => {
    const root = tempRoot();
    const definition = { revision: "b".repeat(40), downloadBytes: 1, modelFile: "model.bin", modelSha256: "0".repeat(64), files: { "model.bin": 1 } };
    await expect(inspectAlignmentModel(root, definition)).resolves.toMatchObject({ installed: false, error: expect.stringMatching(/missing/) });
  });

  it("removes a promoted target immediately when final verification fails", async () => {
    const root = tempRoot();
    const target = path.join(root, "promoted");
    fs.mkdirSync(target);
    fs.writeFileSync(path.join(target, "corrupt.bin"), "corrupt");
    await expect(verifyPromotedAlignmentDirectory(target, async () => ({
      installed: false,
      modelPath: "",
      cachePath: target,
      revision: "a".repeat(40),
      downloadBytes: 7,
      verified: false,
      error: "digest mismatch",
    }))).rejects.toThrow(/digest mismatch/);
    expect(fs.existsSync(target)).toBe(false);
  });
});

function tempRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-alignment-"));
  roots.push(root);
  return root;
}
