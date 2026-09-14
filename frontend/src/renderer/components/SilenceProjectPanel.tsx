import { ArrowDown, ArrowUp, FilePlus2, LoaderCircle, Trash2 } from "lucide-react";
import { useState } from "react";
import { useI18n } from "../i18n";
import { buildEditorialSources, setPairedAudioRole, type EditorialMediaCandidate } from "../lib/editorialPairing";
import type { EditorialProjectRequest } from "../lib/types";

type Props = {
  value: EditorialProjectRequest;
  disabled?: boolean;
  onChange(value: EditorialProjectRequest): void;
  onPrimarySource(path: string): void;
};

export default function SilenceProjectPanel({ value, disabled = false, onChange, onPrimarySource }: Props) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const locked = disabled || busy;
  async function add(paths: string[]) {
    if (locked) return;
    setBusy(true); setError("");
    try {
      const candidates: EditorialMediaCandidate[] = [];
      const unique = new Set([...value.sources.flatMap(s => [s.visualPath, s.audioPath]), ...paths]);
      for (const path of unique) {
        const analysis = await window.subtitler.analyzeMedia(path);
        if (!analysis.videoCodec || !analysis.durationSeconds) throw new Error(t("editorial.badVideo", { path }));
        candidates.push({ path, analysis });
      }
      const sources = buildEditorialSources(candidates).map(source => {
        const previous = value.sources.find(s => s.mode === "paired" && s.roleConfirmed &&
          [s.audioPath, s.visualPath].includes(source.audioPath) && [s.audioPath, s.visualPath].includes(source.visualPath));
        return previous ? setPairedAudioRole(source, previous.audioPath) : source;
      });
      onChange({ ...value, sources });
      if (sources[0]) onPrimarySource(sources[0].visualPath);
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(false); }
  }
  function update(sources: EditorialProjectRequest["sources"]) {
    onChange({ ...value, sources });
    onPrimarySource(sources[0]?.visualPath ?? "");
  }
  function move(index: number, delta: number) {
    const sources = [...value.sources];
    [sources[index], sources[index + delta]] = [sources[index + delta], sources[index]];
    update(sources);
  }
  const choose = async () => { const files = await window.subtitler.chooseInputFiles(value.sources[0]?.visualPath); if (files?.length) await add(files); };
  return <section className="panel editorial-project-panel" onDragOver={e => e.preventDefault()} onDrop={e => {
    e.preventDefault();
    const paths = Array.from(e.dataTransfer.files).map(file => window.subtitler.filePath(file)).filter(Boolean);
    void add(paths);
  }}>
    <div className="panel-title">{t("silenceMarkers.title")}</div>
    <p className="field-help">{t("silenceMarkers.help")}</p>
    {!value.sources.length && <div className="editorial-drop-zone"><FilePlus2 size={28} /><strong>{t("editorial.dropTitle")}</strong>
      <button disabled={locked} onClick={() => void choose()}>{busy ? <LoaderCircle className="spin" size={16} /> : <FilePlus2 size={16} />}{t("editorial.chooseVideos")}</button></div>}
    {value.sources.length > 0 && <>
      <div className="editorial-source-header"><strong>{t("editorial.sourcesOrder")}</strong><button disabled={locked} onClick={() => void choose()}><FilePlus2 size={16} />{t("editorial.addVideos")}</button></div>
      <div className="editorial-source-list">{value.sources.map((source, index) => <div className={`editorial-source-row${source.mode === "paired" ? " paired" : ""}`} key={source.visualPath}>
        <span className="editorial-source-order">{index + 1}</span><span><strong>{source.visualPath.split(/[\\/]/).pop()}{source.mode === "paired" ? ` + ${source.audioPath.split(/[\\/]/).pop()}` : ""}</strong>
          <small>{Math.round(source.durationSeconds / 60)} min</small>
          {source.mode === "paired" && <select aria-label={t("additional.speechTrack")} disabled={locked} value={source.roleConfirmed ? source.audioPath : ""} onChange={e => update(value.sources.map((s, i) => i === index ? setPairedAudioRole(s, e.target.value) : s))}>
            <option value="" disabled>{t("editorial.selectFacecam")}</option>{[source.audioPath, source.visualPath].map(path => <option key={path} value={path}>{path.split(/[\\/]/).pop()}</option>)}
          </select>}
        </span>
        <button className="icon-button" aria-label={t("editorial.moveEarlier")} disabled={locked || index === 0} onClick={() => move(index, -1)}><ArrowUp size={15} /></button>
        <button className="icon-button" aria-label={t("editorial.moveLater")} disabled={locked || index === value.sources.length - 1} onClick={() => move(index, 1)}><ArrowDown size={15} /></button>
        <button className="icon-button" aria-label={t("editorial.removeSource")} disabled={locked} onClick={() => update(value.sources.filter((_, i) => i !== index))}><Trash2 size={15} /></button>
      </div>)}</div>
    </>}
    {error && <div className="field-error">{error}</div>}
  </section>;
}
