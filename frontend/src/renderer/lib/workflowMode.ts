import type { WorkflowName } from "./types";

export function workflowToMode(workflow: WorkflowName): { hosted: boolean; longStream: boolean } {
  return {
    hosted: workflow === "hosted" || workflow === "hosted-long-stream",
    longStream: workflow === "local-long-stream" || workflow === "hosted-long-stream"
  };
}

export function modeToWorkflow(hosted: boolean, longStream: boolean): WorkflowName {
  if (hosted) return longStream ? "hosted-long-stream" : "hosted";
  return longStream ? "local-long-stream" : "local";
}

