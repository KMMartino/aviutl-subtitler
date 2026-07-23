import { describe, expect, it } from "vitest";
import { mediaContentType, resolveByteRange } from "./mediaRange";

describe("private media byte ranges", () => {
  it("resolves bounded, open-ended, and suffix ranges", () => {
    expect(resolveByteRange("bytes=10-19", 100)).toEqual({ start: 10, end: 19 });
    expect(resolveByteRange("bytes=90-", 100)).toEqual({ start: 90, end: 99 });
    expect(resolveByteRange("bytes=-20", 100)).toEqual({ start: 80, end: 99 });
    expect(resolveByteRange("bytes=90-200", 100)).toEqual({ start: 90, end: 99 });
  });

  it("rejects ranges outside the file", () => {
    expect(resolveByteRange("bytes=100-", 100)).toBe("unsatisfiable");
    expect(resolveByteRange("items=1-2", 100)).toBe("unsatisfiable");
  });

  it("reports media content types", () => {
    expect(mediaContentType("preview.mp4")).toBe("video/mp4");
    expect(mediaContentType("source.mkv")).toBe("video/x-matroska");
  });
});
