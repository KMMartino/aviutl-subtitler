import type { WorkflowName } from "./types";

export type SettingsExpansion = {
  localModel: boolean;
  server: boolean;
  python: boolean;
  ffmpeg: boolean;
  alignment: boolean;
  env: boolean;
  cutSilence: boolean;
};

export type WorkflowFamily = "local" | "hosted";

export type SettingsExpansionByFamily = Partial<Record<WorkflowFamily, SettingsExpansion>>;

export function workflowFamily(workflow: WorkflowName): WorkflowFamily {
  return workflow === "local" || workflow === "local-long-stream" ? "local" : "hosted";
}

export function defaultSettingsExpansion(readiness: {
  pythonReady: boolean;
  ffmpegReady: boolean;
  alignmentInstalled: boolean;
  envExists: boolean;
  serverExists: boolean;
}): SettingsExpansion {
  return {
    localModel: true,
    server: !readiness.serverExists,
    python: !readiness.pythonReady,
    ffmpeg: !readiness.ffmpegReady,
    alignment: !readiness.alignmentInstalled,
    env: !readiness.envExists,
    cutSilence: false,
  };
}

export function updateSettingsExpansion(
  current: SettingsExpansion,
  section: keyof SettingsExpansion,
): SettingsExpansion {
  return { ...current, [section]: !current[section] };
}
