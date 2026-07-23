import { describe, expect, it } from "vitest";
import {
  defaultSettingsExpansion,
  updateSettingsExpansion,
  workflowFamily,
  type SettingsExpansionByFamily,
} from "./settingsExpansion";

describe("settings expansion state", () => {
  it("keeps the existing readiness-based defaults", () => {
    expect(defaultSettingsExpansion({ pythonReady: true, ffmpegReady: true, alignmentInstalled: true, envExists: true, serverExists: true })).toEqual({
      localModel: true,
      server: false,
      python: false,
      ffmpeg: false,
      alignment: false,
      env: false,
      cutSilence: false,
    });
  });

  it("retains independent choices for local and hosted workflow families", () => {
    const initial = defaultSettingsExpansion({ pythonReady: true, ffmpegReady: true, alignmentInstalled: true, envExists: true, serverExists: true });
    const states: SettingsExpansionByFamily = {
      local: updateSettingsExpansion(initial, "python"),
      hosted: updateSettingsExpansion(initial, "env"),
    };

    expect(states.local?.python).toBe(true);
    expect(states.hosted?.python).toBe(false);
    expect(states.hosted?.env).toBe(true);
    expect(states.local?.env).toBe(false);
  });

  it("shares expansion state between short and long variants", () => {
    expect(workflowFamily("local")).toBe("local");
    expect(workflowFamily("local-long-stream")).toBe("local");
    expect(workflowFamily("hosted")).toBe("hosted");
    expect(workflowFamily("hosted-long-stream")).toBe("hosted");

    const initial = defaultSettingsExpansion({ pythonReady: true, ffmpegReady: true, alignmentInstalled: true, envExists: true, serverExists: true });
    const states: SettingsExpansionByFamily = {};
    const localFamily = workflowFamily("local-long-stream");
    states[localFamily] = updateSettingsExpansion(states[localFamily] ?? initial, "python");

    expect(states[workflowFamily("local")]?.python).toBe(true);
    expect(states[workflowFamily("hosted")]?.python).toBeUndefined();
  });
});
