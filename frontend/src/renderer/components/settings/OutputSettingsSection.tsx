import { ExternalLink, FolderOpen } from "lucide-react";
import type { CoreWorkflowSettings } from "../../lib/types";
import TooltipLabel from "../TooltipLabel";
import { useI18n } from "../../i18n";

type Props = { settings: CoreWorkflowSettings; enabled: boolean; directory: string; outputPath: string; onChange(settings: CoreWorkflowSettings): void; onDirectory(path: string): void; onEnabled(value: boolean): void };

export default function OutputSettingsSection({ settings, enabled, directory, outputPath, onChange, onDirectory, onEnabled }: Props) {
  const { t } = useI18n();
  const runStem = outputPath.replace(/^.*[\\/]/, "").replace(/\.exo$/i, "");
  const sidecarFile = (suffix: string) => directory && runStem ? `${directory}\\${runStem}${suffix}` : "";
  async function pickDirectory() { const path = await window.subtitler.chooseDirectory(); if (path) onDirectory(path); }
  async function openLocation() { if (directory) await window.subtitler.openPath(await window.subtitler.pathExists(directory) ? directory : parentDirectory(directory)); }
  return <div className="sidecar-settings">
    <span className="field-label-line"><TooltipLabel text={t("settings.sidecarsHelp")}>{t("settings.sidecars")}</TooltipLabel><label className="switch-label"><input className="switch" type="checkbox" checked={enabled} onChange={(event) => onEnabled(event.target.checked)} />{enabled ? t("common.on") : t("common.off")}</label></span>
    {enabled ? <>
      <label><TooltipLabel text={t("settings.sidecarDirectoryHelp")}>{t("settings.sidecarDirectory")}</TooltipLabel><div className="row"><input value={directory} onChange={(event) => onDirectory(event.target.value)} /><button className="icon-button" aria-label={t("settings.chooseSidecar")} onClick={pickDirectory} title={t("settings.chooseSidecar")}><FolderOpen size={17} /></button></div></label>
      <label className="check"><input type="checkbox" checked={settings.diagnostics.profile} onChange={(event) => onChange({ ...settings, diagnostics: { profile: event.target.checked } })} /><TooltipLabel text={t("settings.writeDiagnosticsHelp")}>{t("settings.writeDiagnostics")}</TooltipLabel></label>
      <div className="sidecar-actions">
        <button disabled={!directory} onClick={openLocation}><FolderOpen size={15} /> {t("settings.sidecarLocation")}</button>
        <button disabled={!directory || !runStem} title={t("settings.openRunJson")} onClick={() => window.subtitler.openPath(sidecarFile(".run.json"))}><ExternalLink size={15} /> {t("settings.runJson")}</button>
        <button disabled={!directory || !runStem} title={t("settings.openFinalText")} onClick={() => window.subtitler.openPath(sidecarFile(".final_text.txt"))}><ExternalLink size={15} /> {t("settings.finalText")}</button>
        <button disabled={!directory || !runStem} title={t("settings.openReviewNotes")} onClick={() => window.subtitler.openPath(sidecarFile(".possible_mistranscriptions.txt"))}><ExternalLink size={15} /> {t("settings.reviewNotes")}</button>
      </div>
    </> : <div className="disabled-field">{t("settings.sidecarsDisabled")}</div>}
  </div>;
}

function parentDirectory(value: string): string { return value.replace(/[\\/][^\\/]+[\\/]?$/, ""); }
