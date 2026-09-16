import { useState } from "react";
import { FolderOpen } from "lucide-react";
import { useI18n } from "../i18n";
export type SourceDownload = { url: string; directory: string };
export type SourceUrlProps = { disabled?: boolean; defaultLocation: string; value: SourceDownload; onChange(value: SourceDownload): void };
export default function SourceUrlInput({ disabled, value, onChange, defaultLocation }: SourceUrlProps) {
  const { t } = useI18n();
  const [error, setError] = useState("");
  async function chooseDirectory() {
    try {
      const directory = await window.subtitler.chooseDirectory();
      if (directory) onChange({ ...value, directory });
      setError("");
    } catch (cause) { setError(String(cause)); }
  }
  return <div className="stack">
    <label>{t("input.sourceUrl")}<div className="row">
      <input type="url" value={value.url} disabled={disabled} placeholder="https://…" onChange={(event) => onChange({ ...value, url: event.target.value })} />
      <button disabled={disabled} onClick={() => void chooseDirectory()}><FolderOpen size={16} />{t("input.downloadLocation")}</button>
    </div></label>
    <small className="moment-path">{t("input.downloadFolder", { path: value.directory || defaultLocation })}</small>
    <small>{t("input.downloadStorageHelp")}</small>
    {error && <div className="field-error" role="alert">{error}</div>}
  </div>;
}
