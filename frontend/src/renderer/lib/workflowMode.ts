import type { WorkflowName } from "./types";

export function workflowToMode(workflow: WorkflowName): { hosted: boolean; longStream: boolean } {
  return {
    hosted: workflow === "hosted" || workflow === "hosted-long-stream",
    longStream: workflow === "local-long-stream" || workflow === "hosted-long-stream"
  };
}

export function modeToWorkflow(hosted: boolean, longStream: boolean): WorkflowName {
  if (longStream) return "hosted-long-stream";
  return hosted ? "hosted" : "local";
}
