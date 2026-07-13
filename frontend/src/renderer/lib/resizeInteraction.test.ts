import { describe, expect, it } from "vitest";
import { clampResize, resizeFromKey } from "./resizeInteraction";

describe("resize interaction", () => {
  it("clamps values to the available range", () => {
    expect(clampResize(20, 38, 72)).toBe(38);
    expect(clampResize(80, 38, 72)).toBe(72);
    expect(clampResize(55, 38, 72)).toBe(55);
  });

  it("moves a vertical separator with horizontal arrows", () => {
    expect(resizeFromKey(50, "ArrowLeft", "vertical", 38, 72)).toBe(49);
    expect(resizeFromKey(50, "ArrowRight", "vertical", 38, 72, true)).toBe(55);
    expect(resizeFromKey(50, "ArrowUp", "vertical", 38, 72)).toBeNull();
  });

  it("moves the log separator visually and supports bounds", () => {
    expect(resizeFromKey(24, "ArrowUp", "horizontal", 14, 48)).toBe(25);
    expect(resizeFromKey(24, "ArrowDown", "horizontal", 14, 48)).toBe(23);
    expect(resizeFromKey(24, "Home", "horizontal", 14, 48)).toBe(14);
    expect(resizeFromKey(24, "End", "horizontal", 14, 48)).toBe(48);
  });
});
