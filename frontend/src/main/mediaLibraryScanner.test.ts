import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { enumerateMediaFiles, imageTransparency } from "./mediaLibraryScanner";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("media library scanner", () => {
  it("enumerates supported media without following unrelated files", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "subutl-scan-"));
    roots.push(root);
    fs.mkdirSync(path.join(root, "nested"));
    fs.writeFileSync(path.join(root, "clip.MKV"), "");
    fs.writeFileSync(path.join(root, "notes.txt"), "");
    fs.writeFileSync(path.join(root, "nested", "still.PNG"), "");

    expect(await enumerateMediaFiles(root, true)).toEqual([
      { path: path.join(root, "clip.MKV"), kind: "video" },
      { path: path.join(root, "nested", "still.PNG"), kind: "image" },
    ]);
    expect(await enumerateMediaFiles(root, false)).toEqual([
      { path: path.join(root, "clip.MKV"), kind: "video" },
    ]);
    expect(await enumerateMediaFiles(root, true, 1)).toHaveLength(1);
  });

  it("classifies image alpha-channel support from the decoded pixel format", () => {
    expect(imageTransparency("overlay.png", "image", "rgba")).toBe("present");
    expect(imageTransparency("overlay.webp", "image", "yuva420p")).toBe("present");
    expect(imageTransparency("background.png", "image", "rgb24")).toBe("absent");
    expect(imageTransparency("palette.gif", "image", "pal8")).toBe("unknown");
    expect(imageTransparency("photo.jpg", "image", "rgba")).toBe("unsupported");
    expect(imageTransparency("clip.webm", "video", "yuva420p")).toBe("unsupported");
  });
});
