import type { CoreWorkflowSettings, WorkflowName } from "../lib/types";
import TooltipLabel from "./TooltipLabel";

type Props = { workflow: WorkflowName; settings: CoreWorkflowSettings; disabled?: boolean; onChange(settings: CoreWorkflowSettings): void };

export default function AdditionalSettingsPanel({ workflow, settings, disabled = false, onChange }: Props) {
  const additionalSettings = settings.additionalSettings ?? { youtubeChapters: false };
  return <section className="panel additional-settings-panel">
    <div className="panel-title">Additional Settings</div>
    {workflow === "hosted" ? <label className="check">
      <input disabled={disabled} type="checkbox" checked={additionalSettings.youtubeChapters} onChange={(event) => onChange({ ...settings, additionalSettings: { ...additionalSettings, youtubeChapters: event.target.checked } })} />
      <TooltipLabel text="Use the hosted cleanup model to analyze the full final transcript and add YouTube-style chapter title markers to the EXO output.">YouTube chapter markers</TooltipLabel>
    </label> : <div className="additional-settings-empty">No additional settings</div>}
  </section>;
}
