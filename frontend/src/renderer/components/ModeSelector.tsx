import { Radio, Video, ScanSearch } from "lucide-react";
import type { WorkflowName } from "../lib/types";
import { modeToWorkflow, workflowToMode } from "../lib/workflowMode";
import { useI18n } from "../i18n";

export default function ModeSelector({ workflow, onChange, disabled = false, extraction = false, onExtract }: { workflow: WorkflowName; onChange(value: WorkflowName): void; disabled?: boolean; extraction?: boolean; onExtract?(): void }) {
  const { t } = useI18n();
  const mode = workflowToMode(workflow);
  return (
    <div className="mode-selector">
      <div className="segmented" aria-label={t("mode.mediaLength")}>
        <button disabled={disabled} className={!extraction && !mode.longStream ? "active" : ""} onClick={() => onChange(modeToWorkflow(mode.hosted, false))}><Video size={15} /> {t("mode.short")}</button>
        <button disabled={disabled} className={!extraction && mode.longStream ? "active" : ""} onClick={() => onChange(modeToWorkflow(mode.hosted, true))}><Radio size={15} /> {t("mode.long")}</button>
        <button disabled={disabled} className={extraction ? "active" : ""} onClick={onExtract}><ScanSearch size={15} /> {t("moments.title")}</button>
      </div>
    </div>
  );
}
