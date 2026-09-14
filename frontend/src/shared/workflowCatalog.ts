import type { WorkflowName } from "../renderer/lib/types";

type WorkflowDefinition = {
  engine: "local" | "hosted";
  process: "subtitles" | "editorial";
  outputSuffix: string;
  capabilities: readonly string[];
};

export const workflowCatalog: Record<WorkflowName, WorkflowDefinition> = {
  local: { engine: "local", process: "subtitles", outputSuffix: "", capabilities: ["silence"] },
  hosted: { engine: "hosted", process: "subtitles", outputSuffix: "-hosted", capabilities: ["silence", "broll", "chapters"] },
  "hosted-long-stream": { engine: "hosted", process: "editorial", outputSuffix: "-long-stream-hosted", capabilities: [] },
};

export const workflows = Object.keys(workflowCatalog) as WorkflowName[];
export const isHostedWorkflow = (workflow: WorkflowName): boolean => workflowCatalog[workflow].engine === "hosted";
export const isLocalWorkflow = (workflow: WorkflowName): boolean => workflowCatalog[workflow].engine === "local";
export const supportsWorkflowFeature = (workflow: WorkflowName, feature: string): boolean => workflowCatalog[workflow].capabilities.includes(feature);
