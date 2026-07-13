import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadVerifiedArtifact, verifyArtifact } from "./artifactIntegrity";

const roots: string[] = [];
const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("artifact integrity", () => {
  it("accepts a valid artifact and rejects truncated or corrupt bytes", async () => {
    const file = target("artifact.bin");
    const content = Buffer.from("verified fixture");
    fs.writeFileSync(file, content);
    const expected = expectation(content);
    await expect(verifyArtifact(file, expected)).resolves.toBeUndefined();
    fs.writeFileSync(file, content.subarray(0, 3));
    await expect(verifyArtifact(file, expected)).rejects.toThrow(/size mismatch/);
    fs.writeFileSync(file, Buffer.alloc(content.length, 1));
    await expect(verifyArtifact(file, expected)).rejects.toThrow(/SHA-256 mismatch/);
  });

  it("reuses a verified cache without a request", async () => {
    const content = Buffer.from("cached");
    const file = target("cached.bin");
    fs.writeFileSync(file, content);
    global.fetch = vi.fn();
    await downloadVerifiedArtifact("https://example.test/file", file, expectation(content));
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("invalidates a corrupt cache and recovers from the verified response", async () => {
    const content = Buffer.from("replacement");
    const file = target("recovery.bin");
    fs.writeFileSync(file, Buffer.alloc(content.length, 2));
    global.fetch = vi.fn(async () => response(content)) as typeof fetch;
    await downloadVerifiedArtifact("https://example.test/file", file, expectation(content));
    expect(fs.readFileSync(file)).toEqual(content);
    expect(fs.existsSync(`${file}.part`)).toBe(false);
  });

  it("removes partial data when the server metadata or body is truncated", async () => {
    const content = Buffer.from("complete");
    const file = target("truncated.bin");
    global.fetch = vi.fn(async () => response(content.subarray(0, 2), content.length)) as typeof fetch;
    await expect(downloadVerifiedArtifact("https://example.test/file", file, expectation(content))).rejects.toThrow(/size mismatch/);
    expect(fs.existsSync(file)).toBe(false);
    expect(fs.existsSync(`${file}.part`)).toBe(false);
  });
});

function target(name: string): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-artifact-"));
  roots.push(root);
  return path.join(root, name);
}

function expectation(content: Buffer) {
  return { bytes: content.length, sha256: crypto.createHash("sha256").update(content).digest("hex") };
}

function response(content: Buffer, contentLength = content.length): Response {
  return new Response(content, { status: 200, headers: { "content-length": String(contentLength) } });
}
