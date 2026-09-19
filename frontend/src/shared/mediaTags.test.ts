import { describe, expect, it } from "vitest";
import { normalizeTagValue, parseMediaTag, parseTagQuery } from "./mediaTags";

describe("structured media tags", () => {
  it("normalizes equivalent values and keeps quoted multiword filters separate from text", () => {
    expect(normalizeTagValue("  ＥＬＤＥＮ   Ring ")).toBe("elden ring");
    expect(parseTagQuery('boss game:"Elden Ring" action:dodging')).toEqual({ text: "boss",
      tags: [{ category: "game", value: "Elden Ring" }, { category: "action", value: "dodging" }] });
    expect(parseMediaTag("action:回避")).toEqual({ category: "action", value: "回避" });
  });

  it("validates manual categories and treats old untyped model tags as keywords", () => {
    expect(parseMediaTag("boss fight")).toEqual({ category: "keyword", value: "boss fight" });
    expect(() => parseMediaTag("unknown:value", true)).toThrow();
    expect(() => parseMediaTag("action:", true)).toThrow();
    expect(() => parseMediaTag("a".repeat(161), true)).toThrow();
    expect(() => parseMediaTag("subject:ab\0cd", true)).toThrow();
  });
});
