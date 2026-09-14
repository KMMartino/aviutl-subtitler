import { workflowCatalog, workflows } from "../../shared/workflowCatalog";
import type { WorkflowName } from "./types";

export function workflowToMode(workflow: WorkflowName): { hosted: boolean; longStream: boolean } {
  return { hosted: workflowCatalog[workflow].engine === "hosted", longStream: workflowCatalog[workflow].process === "editorial" };
}

export function modeToWorkflow(hosted: boolean, longStream: boolean): WorkflowName {
  const process = longStream ? "editorial" : "subtitles";
  const engine = hosted ? "hosted" : "local";
  return workflows.find((name) => workflowCatalog[name].process === process && workflowCatalog[name].engine === engine)
    ?? workflows.find((name) => workflowCatalog[name].process === process)!;
}
