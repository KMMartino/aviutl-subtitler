import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { extractZipArchive } from "./archiveExtractor";

const zipFixture =
  "UEsDBBQAAAAIAEW4DV1pGlxIDwAAAA0AAAAJAAAAaGVsbG8udHh0y0jNyclXSCxKzsgsSwUAUEsBAhQAFAAAAAgARbgNXWkaXEgPAAAADQAAAAkAAAAAAAAAAAAAAAAAAAAAAGhlbGxvLnR4dFBLBQYAAAAAAQABADcAAAA2AAAAAAA=";

const temporaryRoots: string[] = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("extractZipArchive", () => {
  it("extracts a downloaded archive into the requested staging directory", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-extract-"));
    temporaryRoots.push(root);
    const zipPath = path.join(root, "fixture.zip");
    const destination = path.join(root, "staging");
    fs.writeFileSync(zipPath, Buffer.from(zipFixture, "base64"));
    fs.mkdirSync(destination);

    await extractZipArchive(zipPath, destination);

    expect(fs.readFileSync(path.join(destination, "hello.txt"), "utf8")).toBe("hello archive");
  });
});
