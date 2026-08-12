import { Play, Square } from "lucide-react";
import StatusBadge from "./StatusBadge";
import type { RunState } from "../lib/types";
import { useI18n } from "../i18n";

type Props = {
  state: RunState;
  elapsed: string;
  canRun: boolean;
  onRun(): void;
  onCancel(): void;
};

export default function RunPanel({ state, elapsed, canRun, onRun, onCancel }: Props) {
  const { t } = useI18n();
  return (
    <section className="panel run-panel">
      <div className="panel-title">{t("run.title")}</div>
      <div className="run-row">
        <StatusBadge state={state} />
        <span className="elapsed">{elapsed}</span>
      </div>
      <div className="row">
        <button className="primary" disabled={!canRun || state === "running"} onClick={onRun}><Play size={16} /> {t("run.start")}</button>
        <button disabled={state !== "running"} onClick={onCancel}><Square size={16} /> {t("run.cancel")}</button>
      </div>
    </section>
  );
}
