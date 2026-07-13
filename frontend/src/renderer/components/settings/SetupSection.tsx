import { AlertTriangle, CheckCircle, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

export type SetupStatus = { kind: "ready" | "warning" | "required"; label: string };

type Props = { title: string; detail: string; ready: boolean; readyLabel?: string; notReadyLabel?: string; status?: SetupStatus; expanded: boolean; onToggle(): void; children: ReactNode };

export default function SetupSection({ title, detail, ready, readyLabel = "Ready", notReadyLabel = "Not ready", status, expanded, onToggle, children }: Props) {
  const displayedStatus = status ?? { kind: ready ? "ready" as const : "required" as const, label: ready ? readyLabel : notReadyLabel };
  return <div className="setup-section">
    <button type="button" className="setup-summary" onClick={onToggle} aria-expanded={expanded}>
      <span><strong>{title}</strong><small>{detail}</small></span>
      <span className={`setup-${displayedStatus.kind}`}>
        {displayedStatus.kind === "ready" ? <CheckCircle size={15} /> : <AlertTriangle size={15} />}
        {displayedStatus.label}<ChevronDown size={16} className={expanded ? "chevron-open" : ""} />
      </span>
    </button>
    <div className={`setup-content-outer ${expanded ? "expanded" : ""}`}><div className="setup-content-clip"><div className="stack setup-content">{children}</div></div></div>
  </div>;
}
