import { describe, expect, it } from "vitest";
import { formatTimecode, parseTimecode } from "./timecodes";

describe("media timecodes", () => {
  it("accepts concise user timecodes and empty components", () => {
    expect(parseTimecode("25")).toBe(25_000);
    expect(parseTimecode(" 1:40 ")).toBe(100_000);
    expect(parseTimecode("1::05")).toBe(3_605_000);
    expect(parseTimecode("", 0)).toBe(0);
  });

  it("rejects ambiguous or invalid ranges", () => {
    expect(() => parseTimecode("1:70")).toThrow();
    expect(() => parseTimecode("1:2:3:4")).toThrow();
  });

  it("formats library milliseconds for editing", () => {
    expect(formatTimecode(25_000)).toBe("0:25");
    expect(formatTimecode(3_723_000)).toBe("1:02:03");
  });
});
