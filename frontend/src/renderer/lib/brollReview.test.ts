import { describe, expect, it } from "vitest";
import { applyBrollDecision } from "./brollReview";
import type { BrollCandidate } from "./types";

const scene = (id: string, startLine = 1): BrollCandidate => ({
  id, assetId: id, assetPath: `C:\\media\\${id}.mp4`, title: id, mediaKind: "video",
  startLine, endLine: startLine, transcriptText: "dodge", sourceStartSec: 10, sourceEndSec: 14,
  confidence: .9, reason: "dodge", descriptionRequired: false,
});

describe("B-roll scene choices", () => {
  it("switches alternatives without leaving two selected for the same passage", () => {
    const candidates = [scene("first"), scene("alternative"), scene("later", 5)];
    const first = applyBrollDecision({}, candidates, candidates[0], { candidateId: "first", decision: "use_library" });
    const changed = applyBrollDecision(first, candidates, candidates[1], { candidateId: "alternative", decision: "use_library" });
    expect(changed.first.decision).toBe("reject");
    expect(changed.alternative.decision).toBe("use_library");
    expect(changed.later).toBeUndefined();
    expect(first.first.decision).toBe("use_library");
  });

  it("allows all candidates to be rejected", () => {
    const candidates = [scene("first"), scene("alternative")];
    const first = applyBrollDecision({}, candidates, candidates[0], { candidateId: "first", decision: "reject" });
    const next = applyBrollDecision(first, candidates, candidates[1], { candidateId: "alternative", decision: "reject" });
    expect(Object.values(next).every((decision) => decision.decision === "reject")).toBe(true);
  });
});
