import type { CoreWorkflowSettings, LocalModelProfile, LocalModelStatus } from "./types";

export function matchesLocalProfile(core: CoreWorkflowSettings, status: LocalModelStatus, profile: LocalModelProfile | undefined): boolean {
  if (!core.local) return false;
  const files = status.files;
  return core.local.model === files.transcription.path
    && core.local.mmproj === files.projector.path
    && core.local.cleanupModel === files.cleanup.path
    && core.local.transcriptionDraftModel === (files.transcriptionDraft?.path ?? "")
    && core.local.cleanupDraftModel === (files.cleanupDraft?.path ?? "")
    && core.cleanupGroupPolicy?.minSec === profile?.cleanupGroupPolicy.minSec
    && core.cleanupGroupPolicy?.durationDivisor === profile?.cleanupGroupPolicy.durationDivisor
    && core.cleanupGroupPolicy?.maxSec === profile?.cleanupGroupPolicy.maxSec;
}

export function applyLocalProfile(core: CoreWorkflowSettings, status: LocalModelStatus, profile: LocalModelProfile | undefined): CoreWorkflowSettings {
  if (!core.local) return core;
  const files = status.files;
  return {
    ...core,
    cleanupGroupPolicy: profile?.cleanupGroupPolicy ?? core.cleanupGroupPolicy,
    local: {
      ...core.local,
      model: files.transcription.path,
      mmproj: files.projector.path,
      cleanupModel: files.cleanup.path,
      transcriptionDraftModel: files.transcriptionDraft?.path ?? "",
      cleanupDraftModel: files.cleanupDraft?.path ?? ""
    }
  };
}
