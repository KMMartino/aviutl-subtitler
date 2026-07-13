import { describe, expect, it } from "vitest";
import { applyLocalProfile, matchesLocalProfile } from "./localProfileSettings";
import type { CoreWorkflowSettings, LocalModelProfile, LocalModelStatus } from "./types";

describe("local profile settings", () => {
  it("synchronizes all model artifacts and cleanup group policy", () => {
    const core = makeCore();
    const status = makeStatus();
    const profile = makeProfile();

    const next = applyLocalProfile(core, status, profile);

    expect(next.local).toMatchObject({
      model: "transcription.gguf",
      mmproj: "projector.gguf",
      cleanupModel: "cleanup.gguf",
      transcriptionDraftModel: "transcription-draft.gguf",
      cleanupDraftModel: "cleanup-draft.gguf",
    });
    expect(next.cleanupGroupPolicy).toEqual({ minSec: 40, durationDivisor: 4, maxSec: 300 });
    expect(matchesLocalProfile(next, status, profile)).toBe(true);
  });

  it("clears optional draft paths and detects stale settings", () => {
    const core = makeCore();
    const status = makeStatus();
    status.files.transcriptionDraft = undefined;
    status.files.cleanupDraft = undefined;

    expect(matchesLocalProfile(core, status, makeProfile())).toBe(false);
    expect(applyLocalProfile(core, status, makeProfile()).local).toMatchObject({
      transcriptionDraftModel: "",
      cleanupDraftModel: "",
    });
  });
});

function makeCore(): CoreWorkflowSettings {
  return {
    audioTrack: 0,
    alignment: { model: "alignment", offlineModelCache: false },
    diagnostics: { profile: false },
    local: {
      model: "old-transcription.gguf",
      mmproj: "old-projector.gguf",
      llamaServer: "llama-server.exe",
      cleanupModel: "old-cleanup.gguf",
      cleanupLlamaServer: "llama-server.exe",
      transcriptionDraftModel: "old-transcription-draft.gguf",
      cleanupDraftModel: "old-cleanup-draft.gguf",
    },
  };
}

function makeStatus(): LocalModelStatus {
  return {
    profile: "12gb-gpu-gemma",
    installed: true,
    managed: true,
    needsVerification: false,
    downloading: false,
    files: {
      transcription: { path: "transcription.gguf", exists: true },
      projector: { path: "projector.gguf", exists: true },
      cleanup: { path: "cleanup.gguf", exists: true },
      transcriptionDraft: { path: "transcription-draft.gguf", exists: true },
      cleanupDraft: { path: "cleanup-draft.gguf", exists: true },
    },
  };
}

function makeProfile(): LocalModelProfile {
  return {
    id: "12gb-gpu-gemma",
    label: "12 GB",
    summary: "test",
    vramGb: 12,
    experimental: false,
    downloadBytes: 1,
    cleanupGroupPolicy: { minSec: 40, durationDivisor: 4, maxSec: 300 },
  };
}
