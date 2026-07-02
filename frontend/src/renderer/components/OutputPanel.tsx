import { ChevronDown, ExternalLink, FolderOpen } from "lucide-react";
import { useState } from "react";

type Props = {
  outputPath: string;
  sidecarDir: string;
  sidecarsEnabled: boolean;
  onOutput(path: string): void;
};

export default function OutputPanel({ outputPath, sidecarDir, sidecarsEnabled, onOutput }: Props) {
  const [expanded, setExpanded] = useState(true);
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
  return (
    <section className={`panel output-panel ${expanded ? "expanded" : "collapsed"}`}>
      <button type="button" className="panel-summary" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        <span>Outputs</span>
        <ChevronDown size={16} className={expanded ? "chevron-open" : ""} />
      </button>
      <div className="collapsible-panel-body">
        <label>
          <span className="field-label">EXO file</span>
          <div className="row">
            <input value={outputPath} onChange={(event) => onOutput(event.target.value)} />
            <button className="icon-button" disabled={!outputPath} onClick={() => window.subtitler.showItemInFolder(outputPath)} title="Open EXO location"><FolderOpen size={17} /></button>
          </div>
        </label>
        <div className="button-grid">
          {sidecarsEnabled && <button disabled={!sidecarDir} onClick={openSidecarLocation}><FolderOpen size={15} /> Sidecar location</button>}
          {sidecarsEnabled && <button disabled={!sidecarDir} title="Open the per-run JSON summary with command inputs, paths, configuration, timings, and status details." onClick={() => window.subtitler.openPath(sidecarFile(".run.json"))}><ExternalLink size={15} /> Run JSON</button>}
          {sidecarsEnabled && <button disabled={!sidecarDir} title="Open the final cleaned transcript text that was used to produce subtitle output." onClick={() => window.subtitler.openPath(sidecarFile(".final_text.txt"))}><ExternalLink size={15} /> Final text</button>}
          {sidecarsEnabled && <button disabled={!sidecarDir} title="Open possible mistranscription notes flagged during review for manual checking." onClick={() => window.subtitler.openPath(sidecarFile(".possible_mistranscriptions.txt"))}><ExternalLink size={15} /> Review notes</button>}
        </div>
        {!sidecarsEnabled && <div className="disabled-field">Sidecar files are disabled in Settings</div>}
      </div>
    </section>
  );
}

function parentDirectory(value: string): string {
  const normalized = value.replace(/[\\/]+$/, "");
  const index = Math.max(normalized.lastIndexOf("\\"), normalized.lastIndexOf("/"));
  return index > 0 ? normalized.slice(0, index) : normalized;
}
