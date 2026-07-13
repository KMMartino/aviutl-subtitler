import { describe, expect, it } from "vitest";
import { LogBuffer } from "./logBuffer";

describe("LogBuffer", () => {
  it("keeps recent output within the configured cap", () => {
    const buffer = new LogBuffer(10);
    buffer.append("12345");
    buffer.append("67890");
    buffer.append("abc");
    expect(buffer.value()).toBe("4567890abc");
    expect(buffer.size).toBe(10);
  });

  it("handles a sustained high-volume session without growing", () => {
    const buffer = new LogBuffer(64 * 1024);
    for (let index = 0; index < 100_000; index += 1) buffer.append(`line ${index}\n`);
    const visible = buffer.value();
    expect(visible.length).toBe(64 * 1024);
    expect(visible).toContain("line 99999");
    expect(visible).not.toContain("line 0\n");
  });

  it("replaces and clears content", () => {
    const buffer = new LogBuffer(5);
    buffer.replace("abcdef");
    expect(buffer.value()).toBe("bcdef");
    buffer.clear();
    expect(buffer.value()).toBe("");
  });
});
