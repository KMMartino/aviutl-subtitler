import { Cloud, HardDrive, Radio, Video } from "lucide-react";
import type { WorkflowName } from "../lib/types";
import { modeToWorkflow, workflowToMode } from "../lib/workflowMode";
import { useI18n } from "../i18n";

export default function ModeSelector({ workflow, onChange, disabled = false }: { workflow: WorkflowName; onChange(value: WorkflowName): void; disabled?: boolean }) {
  const { t } = useI18n();
  const mode = workflowToMode(workflow);
  return (
    <div className="mode-selector">
      <div className="segmented" aria-label={t("mode.processingLocation")}>
        <button disabled={disabled} className={!mode.hosted ? "active" : ""} onClick={() => onChange(modeToWorkflow(false, mode.longStream))}><HardDrive size={15} /> {t("mode.local")}</button>
        <button disabled={disabled} className={mode.hosted ? "active" : ""} onClick={() => onChange(modeToWorkflow(true, mode.longStream))}><Cloud size={15} /> {t("mode.hosted")}</button>
      </div>
      <div className="segmented" aria-label={t("mode.mediaLength")}>
        <button disabled={disabled} className={!mode.longStream ? "active" : ""} onClick={() => onChange(modeToWorkflow(mode.hosted, false))}><Video size={15} /> {t("mode.short")}</button>
        <button disabled={disabled} className={mode.longStream ? "active" : ""} onClick={() => onChange(modeToWorkflow(mode.hosted, true))}><Radio size={15} /> {t("mode.long")}</button>
      </div>
    </div>
  );
}
