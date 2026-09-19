import { describe, expect, it } from "vitest";
import { selectSearchTags } from "./mediaTags";

describe("automatic search tags", () => {
  it("keeps specific retrieval phrases without decorative prose or duplicates", () => {
    expect(selectSearchTags(["game", "announcement", "game announcement", "colorful", "interior",
      "tone:dramatic", "role:atmosphere", "game:Concord", "keyword:Concord", "platform:PlayStation",
      "cinematic trailer", "trailer", "game announcement"])).toEqual([
      { category: "game", value: "Concord" }, { category: "platform", value: "PlayStation" },
      { category: "keyword", value: "game announcement" }, { category: "keyword", value: "cinematic trailer" },
    ]);
  });
  it("bounds automatic tags and accepts empty evidence", () => {
    expect(selectSearchTags([])).toEqual([]);
    expect(selectSearchTags(Array.from({ length: 30 }, (_, i) => `subject:subject ${i}`))).toHaveLength(8);
  });
});
