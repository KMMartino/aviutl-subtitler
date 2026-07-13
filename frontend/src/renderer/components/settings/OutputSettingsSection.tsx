import { ExternalLink, FolderOpen } from "lucide-react";
import type { CoreWorkflowSettings } from "../../lib/types";
import TooltipLabel from "../TooltipLabel";

type Props = { settings: CoreWorkflowSettings; enabled: boolean; directory: string; outputPath: string; onChange(settings: CoreWorkflowSettings): void; onDirectory(path: string): void; onEnabled(value: boolean): void };

export default function OutputSettingsSection({ settings, enabled, directory, outputPath, onChange, onDirectory, onEnabled }: Props) {
  const runStem = outputPath.replace(/^.*[\\/]/, "").replace(/\.exo$/i, "");
  const sidecarFile = (suffix: string) => directory && runStem ? `${directory}\\${runStem}${suffix}` : "";
  async function pickDirectory() { const path = await window.subtitler.chooseDirectory(); if (path) onDirectory(path); }
  async function openLocation() { if (directory) await window.subtitler.openPath(await window.subtitler.pathExists(directory) ? directory : parentDirectory(directory)); }
  return <div className="sidecar-settings">
    <span className="field-label-line"><TooltipLabel text="Sidecar files are auxiliary run outputs such as run JSON, final cleaned text, review notes, and optional diagnostics.">Sidecar files</TooltipLabel><label className="switch-label"><input className="switch" type="checkbox" checked={enabled} onChange={(event) => onEnabled(event.target.checked)} />{enabled ? "On" : "Off"}</label></span>
    {enabled ? <>
      <label><TooltipLabel text="Directory where debugging sidecar files are written.">Sidecar directory</TooltipLabel><div className="row"><input value={directory} onChange={(event) => onDirectory(event.target.value)} /><button className="icon-button" aria-label="Choose sidecar directory" onClick={pickDirectory} title="Choose sidecar directory"><FolderOpen size={17} /></button></div></label>
      <label className="check"><input type="checkbox" checked={settings.diagnostics.profile} onChange={(event) => onChange({ ...settings, diagnostics: { profile: event.target.checked } })} /><TooltipLabel text="Add timing and profiling diagnostics to the sidecar output set.">Write diagnostics</TooltipLabel></label>
      <div className="sidecar-actions">
        <button disabled={!directory} onClick={openLocation}><FolderOpen size={15} /> Sidecar location</button>
        <button disabled={!directory || !runStem} title="Open the per-run JSON summary with command inputs, paths, configuration, timings, and status details." onClick={() => window.subtitler.openPath(sidecarFile(".run.json"))}><ExternalLink size={15} /> Run JSON</button>
        <button disabled={!directory || !runStem} title="Open the final cleaned transcript text that was used to produce subtitle output." onClick={() => window.subtitler.openPath(sidecarFile(".final_text.txt"))}><ExternalLink size={15} /> Final text</button>
        <button disabled={!directory || !runStem} title="Open possible mistranscription notes flagged during review for manual checking." onClick={() => window.subtitler.openPath(sidecarFile(".possible_mistranscriptions.txt"))}><ExternalLink size={15} /> Review notes</button>
      </div>
    </> : <div className="disabled-field">Run JSON, final text, review notes, and diagnostics will not be written.</div>}
  </div>;
}

function parentDirectory(value: string): string { return value.replace(/[\\/][^\\/]+[\\/]?$/, ""); }
