import { Info } from "lucide-react";
import type { ReactNode } from "react";

export default function TooltipLabel({ children, text }: { children: ReactNode; text: string }) {
  return (
    <span className="field-label">
      {children}
      <span className="tooltip" tabIndex={0}>
        <Info size={14} />
        <span className="tooltip-content">{text}</span>
      </span>
    </span>
  );
}

