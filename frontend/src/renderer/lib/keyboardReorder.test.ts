import { describe, expect, it } from "vitest";
import { keyboardReorder } from "./keyboardReorder";

describe("keyboard reordering", () => {
  it("moves an item one place in either direction", () => {
    expect(keyboardReorder(["a", "b", "c"], 1, "ArrowLeft")).toEqual(["b", "a", "c"]);
    expect(keyboardReorder(["a", "b", "c"], 1, "ArrowRight")).toEqual(["a", "c", "b"]);
  });

  it("moves an item to either edge", () => {
    expect(keyboardReorder(["a", "b", "c"], 1, "Home")).toEqual(["b", "a", "c"]);
    expect(keyboardReorder(["a", "b", "c"], 1, "End")).toEqual(["a", "c", "b"]);
  });

  it("ignores unrelated keys and unavailable moves", () => {
    expect(keyboardReorder(["a", "b"], 0, "Enter")).toBeNull();
    expect(keyboardReorder(["a", "b"], 0, "ArrowLeft")).toBeNull();
  });
});
