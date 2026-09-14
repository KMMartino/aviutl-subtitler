import { useEffect, useState } from "react";
import { useI18n } from "../i18n";

export default function SourceUrlInput({ disabled, onInput, onBusyChange }: { disabled?: boolean; onInput(path: string): void | Promise<void>; onBusyChange?(busy: boolean): void }) {
  const { t } = useI18n();
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [percent, setPercent] = useState<number | null>(null);
  useEffect(() => {
    if (loading) return window.subtitler.onSourceProgress(setPercent);
  }, [loading]);
  async function acquire() {
    setLoading(true);
    onBusyChange?.(true);
    setError("");
    setPercent(null);
    try {
      const source = await window.subtitler.acquireSource(url.trim());
      await onInput(source.path);
      setUrl("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
      onBusyChange?.(false);
    }
  }
  return <div className="stack">
    <label>{t("input.sourceUrl")}
      <div className="row">
        <input type="url" value={url} disabled={disabled || loading} placeholder="https://…" onChange={(event) => setUrl(event.target.value)} />
        <button disabled={disabled || loading || !url.trim()} onClick={acquire}>{t(loading ? "input.downloadingSource" : "input.downloadSource")}</button>
        {loading && <button onClick={() => void window.subtitler.cancelSourceAcquisition()}>{t("run.cancel")}</button>}
      </div>
    </label>
    {loading && percent !== null && <div role="status">
      {t("input.sourceProgress", { percent: Math.floor(percent) })}
      <progress value={percent} max={100} aria-label={t("input.downloadingSource")} />
    </div>}
    {error && <div className="field-error" role="alert">{error}</div>}
  </div>;
}
