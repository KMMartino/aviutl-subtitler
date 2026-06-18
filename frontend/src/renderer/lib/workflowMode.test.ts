import { describe, expect, it } from "vitest";
import { modeToWorkflow, workflowToMode } from "./workflowMode";

describe("workflow mode mapping", () => {
  it("maps all two-axis combinations", () => {
    expect(modeToWorkflow(false, false)).toBe("local");
    expect(modeToWorkflow(true, false)).toBe("hosted");
    expect(modeToWorkflow(false, true)).toBe("local-long-stream");
    expect(modeToWorkflow(true, true)).toBe("hosted-long-stream");
  });

  it("round trips a workflow", () => {
    expect(workflowToMode("hosted-long-stream")).toEqual({ hosted: true, longStream: true });
  });
});

