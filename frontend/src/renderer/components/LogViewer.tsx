import { Copy, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type Props = {
  logs: string;
  onClear(): void;
};

export default function LogViewer({ logs, onClear }: Props) {
  const [autoScroll, setAutoScroll] = useState(true);
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (autoScroll && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [logs, autoScroll]);
  return (
    <section className="panel log-panel">
      <div className="panel-title">
        Logs
        <span className="panel-actions">
          <label className="check compact"><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} /> Auto-scroll</label>
          <button onClick={() => navigator.clipboard.writeText(logs)}><Copy size={15} /> Copy</button>
          <button onClick={onClear}><Trash2 size={15} /> Clear</button>
        </span>
      </div>
      <pre ref={ref}>{logs}</pre>
    </section>
  );
}
