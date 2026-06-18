import { ExternalLink, FolderOpen } from "lucide-react";

type Props = {
  outputPath: string;
  sidecarDir: string;
  sidecarsEnabled: boolean;
  onOutput(path: string): void;
  onSidecar(path: string): void;
  onSidecarsEnabled(value: boolean): void;
};

export default function OutputPanel({ outputPath, sidecarDir, sidecarsEnabled, onOutput, onSidecar, onSidecarsEnabled }: Props) {
  const runStem = outputPath.replace(/^.*[\\/]/, "").replace(/\.exo$/i, "");
  const sidecarFile = (suffix: string) => sidecarDir ? `${sidecarDir}\\${runStem}${suffix}` : "";
  async function openSidecarLocation() {
    if (!sidecarDir) return;
    if (await window.subtitler.pathExists(sidecarDir)) {
      await window.subtitler.openPath(sidecarDir);
    } else {
      await window.subtitler.openPath(parentDirectory(sidecarDir));
    }
  }
  async function pickSidecar() {
    const path = await window.subtitler.chooseDirectory();
    if (path) onSidecar(path);
  }
  return (
    <section className="panel output-panel">
      <div className="panel-title">Outputs</div>
      <label>
        <span className="field-label">EXO file</span>
        <div className="row">
          <input value={outputPath} onChange={(event) => onOutput(event.target.value)} />
          <button className="icon-button" disabled={!outputPath} onClick={() => window.subtitler.showItemInFolder(outputPath)} title="Open EXO location"><FolderOpen size={17} /></button>
        </div>
      </label>
      <div className="field-group output-sidecars">
        <span className="field-label-line">
          <span className="field-label">Sidecar files</span>
          <label className="switch-label">
            <input className="switch" type="checkbox" checked={sidecarsEnabled} onChange={(event) => onSidecarsEnabled(event.target.checked)} />
            {sidecarsEnabled ? "On" : "Off"}
          </label>
        </span>
        {sidecarsEnabled ? <div className="row">
          <input value={sidecarDir} onChange={(event) => onSidecar(event.target.value)} />
          <button className="icon-button" onClick={pickSidecar} title="Choose sidecar directory"><FolderOpen size={17} /></button>
        </div> : <div className="disabled-field">Sidecar output disabled</div>}
      </div>
      <div className="button-grid">
        {sidecarsEnabled && <button disabled={!sidecarDir} onClick={openSidecarLocation}><FolderOpen size={15} /> Sidecar location</button>}
        {sidecarsEnabled && <button disabled={!sidecarDir} onClick={() => window.subtitler.openPath(sidecarFile(".run.json"))}><ExternalLink size={15} /> Run JSON</button>}
        {sidecarsEnabled && <button disabled={!sidecarDir} onClick={() => window.subtitler.openPath(sidecarFile(".final_text.txt"))}><ExternalLink size={15} /> Final text</button>}
        {sidecarsEnabled && <button disabled={!sidecarDir} onClick={() => window.subtitler.openPath(sidecarFile(".possible_mistranscriptions.txt"))}><ExternalLink size={15} /> Review notes</button>}
      </div>
    </section>
  );
}

function parentDirectory(value: string): string {
  const normalized = value.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("\\"), normalized.lastIndexOf("/"));
  return index > 0 ? normalized.slice(0, index) : normalized;
}
