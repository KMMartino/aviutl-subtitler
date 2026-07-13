import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { Readable, Transform } from "node:stream";

export type ArtifactExpectation = {
  bytes: number;
  sha256: string;
};

export type ArtifactMetadata = ArtifactExpectation & {
  source: string;
  installedAt: string;
  revision?: string;
};

export async function sha256File(file: string): Promise<string> {
  const hash = crypto.createHash("sha256");
  await pipeline(fs.createReadStream(file), hash);
  return hash.digest("hex");
}

export async function verifyArtifact(file: string, expected: ArtifactExpectation): Promise<void> {
  if (!Number.isSafeInteger(expected.bytes) || expected.bytes <= 0) throw new Error("Artifact metadata has an invalid size.");
  if (!/^[a-f0-9]{64}$/i.test(expected.sha256)) throw new Error("Artifact metadata has an invalid SHA-256 digest.");
  const actualBytes = fs.statSync(file).size;
  if (actualBytes !== expected.bytes) throw new Error(`Artifact size mismatch: expected ${expected.bytes} bytes, received ${actualBytes}.`);
  const actualSha256 = await sha256File(file);
  if (actualSha256.toLowerCase() !== expected.sha256.toLowerCase()) {
    throw new Error(`Artifact SHA-256 mismatch: expected ${expected.sha256}, received ${actualSha256}.`);
  }
}

export async function downloadVerifiedArtifact(
  url: string,
  target: string,
  expected: ArtifactExpectation,
  onProgress: (downloaded: number, total: number) => void = () => undefined,
): Promise<void> {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  if (fs.existsSync(target)) {
    try {
      await verifyArtifact(target, expected);
      return;
    } catch {
      fs.rmSync(target, { force: true });
    }
  }
  const partial = `${target}.part`;
  fs.rmSync(partial, { force: true });
  try {
    const response = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(4 * 60 * 60 * 1000) });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const contentLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(contentLength) && contentLength > 0 && contentLength !== expected.bytes) {
      throw new Error(`Server size mismatch: expected ${expected.bytes} bytes, server reported ${contentLength}.`);
    }
    let downloaded = 0;
    const progress = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        downloaded += chunk.length;
        onProgress(downloaded, expected.bytes);
        callback(null, chunk);
      },
    });
    await pipeline(Readable.fromWeb(response.body as never), progress, fs.createWriteStream(partial));
    await verifyArtifact(partial, expected);
    fs.renameSync(partial, target);
  } catch (error) {
    fs.rmSync(partial, { force: true });
    throw error;
  }
}

export function writeArtifactMetadata(file: string, metadata: ArtifactMetadata): void {
  const temporary = `${file}.tmp`;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, file);
}
