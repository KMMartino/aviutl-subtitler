import { Copy, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";

type Props = {
  logs: string;
  onClear(): void;
};

export default function LogViewer({ logs, onClear }: Props) {
  const { t } = useI18n();
  const [autoScroll, setAutoScroll] = useState(true);
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (autoScroll && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [logs, autoScroll]);
  return (
    <section className="panel log-panel">
      <div className="panel-title">
        {t("logs.title")}
        <span className="panel-actions">
          <label className="check compact"><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} /> {t("logs.autoScroll")}</label>
          <button onClick={() => navigator.clipboard.writeText(logs)}><Copy size={15} /> {t("common.copy")}</button>
          <button onClick={onClear}><Trash2 size={15} /> {t("common.clear")}</button>
        </span>
      </div>
      <pre ref={ref}>{logs}</pre>
    </section>
  );
}
