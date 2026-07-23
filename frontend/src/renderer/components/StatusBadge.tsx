import { AlertTriangle, CheckCircle, Circle, Loader2, Scissors, Square } from "lucide-react";
import type { RunState } from "../lib/types";

type Props = {
  state: RunState;
};

export default function StatusBadge({ state }: Props) {
  const icon = {
    idle: <Circle size={14} />,
    running: <Loader2 size={14} className="spin" />,
    reviewing: <Scissors size={14} />,
    succeeded: <CheckCircle size={14} />,
    failed: <AlertTriangle size={14} />,
    cancelled: <Square size={14} />
  }[state];
  return <span className={`status status-${state}`} role="status" aria-live="polite">{icon}{state}</span>;
}
