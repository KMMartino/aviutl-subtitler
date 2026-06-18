import type { WorkflowName } from "./types";

export const workflows: WorkflowName[] = ["local", "hosted", "local-long-stream", "hosted-long-stream"];

export const workflowLabels: Record<WorkflowName, string> = {
  local: "Local",
  hosted: "Hosted",
  "local-long-stream": "Local Long Stream",
  "hosted-long-stream": "Hosted Long Stream"
};

export const workflowDescriptions: Record<WorkflowName, string> = {
  local: "Local Gemma transcription and local cleanup",
  hosted: "Gemini transcription and OpenAI cleanup",
  "local-long-stream": "Local transcription for selected high-activation speech",
  "hosted-long-stream": "Hosted transcription for selected high-activation speech"
};

export function isHostedWorkflow(workflow: WorkflowName): boolean {
  return workflow === "hosted" || workflow === "hosted-long-stream";
}

export function isLocalWorkflow(workflow: WorkflowName): boolean {
  return workflow === "local" || workflow === "local-long-stream";
}
