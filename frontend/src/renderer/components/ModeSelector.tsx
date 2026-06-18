import { Cloud, HardDrive, Radio, Video } from "lucide-react";
import type { WorkflowName } from "../lib/types";
import { modeToWorkflow, workflowToMode } from "../lib/workflowMode";

export default function ModeSelector({ workflow, onChange }: { workflow: WorkflowName; onChange(value: WorkflowName): void }) {
  const mode = workflowToMode(workflow);
  return (
    <div className="mode-selector">
      <div className="segmented" aria-label="Processing location">
        <button className={!mode.hosted ? "active" : ""} onClick={() => onChange(modeToWorkflow(false, mode.longStream))}><HardDrive size={15} /> Local</button>
        <button className={mode.hosted ? "active" : ""} onClick={() => onChange(modeToWorkflow(true, mode.longStream))}><Cloud size={15} /> Hosted</button>
      </div>
      <div className="segmented" aria-label="Media length mode">
        <button className={!mode.longStream ? "active" : ""} onClick={() => onChange(modeToWorkflow(mode.hosted, false))}><Video size={15} /> Short video</button>
        <button className={mode.longStream ? "active" : ""} onClick={() => onChange(modeToWorkflow(mode.hosted, true))}><Radio size={15} /> Long stream</button>
      </div>
    </div>
  );
}

