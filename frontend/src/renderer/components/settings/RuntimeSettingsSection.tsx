import { AlertTriangle, CheckCircle, Download, FolderOpen, RefreshCw, Trash2 } from "lucide-react";
import type { CoreWorkflowSettings, RuntimeSetupStatus } from "../../lib/types";
import type { SettingsExpansion } from "../../lib/settingsExpansion";
import SetupSection, { type SetupStatus } from "./SetupSection";
import TooltipLabel from "../TooltipLabel";
import { useI18n } from "../../i18n";
import type { TranslationKey, TranslationParameters } from "../../../shared/i18n";

type T = (key: TranslationKey, parameters?: TranslationParameters) => string;

type CookiesBrowser = "" | "brave" | "chrome" | "chromium" | "edge" | "firefox" | "opera" | "safari" | "vivaldi" | "whale";
type Feedback = { section: "python" | "ffmpeg" | "ytDlp" | "alignment"; text: string; ok: boolean } | null;

type Props = {
  settings: CoreWorkflowSettings;
  pythonPath: string;
  pythonReady: boolean;
  runtimeStatus: RuntimeSetupStatus | null;
  runtimeAction: string;
  runtimeFeedback: Feedback;
  expansion: SettingsExpansion;
  onToggle(section: "python" | "ffmpeg" | "ytDlp" | "alignment"): void;
  onPythonPath(path: string): void;
  onRefresh(action?: string, feedbackSection?: "python" | "ffmpeg" | "ytDlp" | "alignment"): void;
  onCreatePython(): void;
  onInstallPythonRequirements(): void;
  onDeletePython(): void;
  onDownloadFfmpeg(): void;
  onDeleteFfmpeg(): void;
  ytDlpDenoPath: string;
  ytDlpCookiesBrowser: CookiesBrowser;
  ytDlpCookiesProfile: string;
  onInstallOrUpdateYtDlp(): void;
  onDeleteYtDlp(): void;
  onYtDlpDenoPath(value: string): void;
  onYtDlpCookiesBrowser(value: CookiesBrowser): void;
  onYtDlpCookiesProfile(value: string): void;
  onDownloadAlignment(): void;
  onDeleteAlignment(): void;
};

export default function RuntimeSettingsSection({ settings, pythonPath, pythonReady, runtimeStatus, runtimeAction, runtimeFeedback, expansion, onToggle, onPythonPath, onRefresh, onCreatePython, onInstallPythonRequirements, onDeletePython, onDownloadFfmpeg, onDeleteFfmpeg, ytDlpDenoPath, ytDlpCookiesBrowser, ytDlpCookiesProfile, onInstallOrUpdateYtDlp, onDeleteYtDlp, onYtDlpDenoPath, onYtDlpCookiesBrowser, onYtDlpCookiesProfile, onDownloadAlignment, onDeleteAlignment }: Props) {
  const { t } = useI18n();
  const busy = Boolean(runtimeAction);
  const alignmentDownloaded = Boolean(runtimeStatus?.alignment.installed);
  const alignmentReady = Boolean(alignmentDownloaded && runtimeStatus?.alignment.modelPath && settings.alignment?.model === runtimeStatus.alignment.modelPath && settings.alignment?.offlineModelCache);
  const alignmentStatus: SetupStatus = alignmentReady ? { kind: "ready", label: t("common.ready") } : alignmentDownloaded ? { kind: "warning", label: t("settings.runtime.downloaded") } : { kind: "required", label: t("common.notInstalled") };
  async function pickPython() { const path = await window.subtitler.chooseExecutable(); if (path) onPythonPath(path); }
  async function pickDeno() { const path = await window.subtitler.chooseExecutable(); if (path) onYtDlpDenoPath(path); }

  return <>
    <SetupSection title={t("settings.runtime.python")} detail={pythonRuntimeSummary(runtimeStatus, t)} ready={pythonReady} expanded={expansion.python} onToggle={() => onToggle("python")}>
      <RuntimeLine label={t("settings.runtime.active")} value={runtimeStatus?.python.ready ? `${runtimeSourceText(runtimeStatus.python.source, t)} · ${runtimeStatus.python.version}` : t("settings.runtime.notFound")} ok={Boolean(runtimeStatus?.python.ready)} />
      <RuntimeLine label={t("settings.runtime.path")} value={runtimeStatus?.python.resolvedPath || t("settings.runtime.noPython")} ok={Boolean(runtimeStatus?.python.ready)} />
      <RuntimeLine label={t("settings.runtime.appDeps")} value={runtimeStatus?.python.requirementsInstalled ? t("common.installed") : t("settings.runtime.missingAligner")} ok={Boolean(runtimeStatus?.python.requirementsInstalled)} />
      <PathInput value={pythonPath} onChange={onPythonPath} onPick={pickPython} />
      {pythonPath && <div className="runtime-actions"><button onClick={() => onPythonPath("")} disabled={busy}>{t("settings.runtime.useAuto")}</button></div>}
      <div className="runtime-actions">
        <button onClick={() => onRefresh("refresh-python", "python")} disabled={busy}>{runtimeAction === "refresh-python" ? <LoadingDots /> : <RefreshCw size={16} />}{runtimeAction === "refresh-python" ? t("settings.runtime.refreshing") : t("settings.runtime.refresh")}</button>
        {!runtimeStatus?.python.managedInstalled && <button onClick={onCreatePython} disabled={busy}>{runtimeAction === "create-python" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "create-python" ? t("settings.runtime.creatingVenv") : t("settings.runtime.createVenv")}</button>}
        {runtimeStatus?.python.source === "managed" && !runtimeStatus.python.requirementsInstalled && <button onClick={onInstallPythonRequirements} disabled={busy || !runtimeStatus.python.ready}>{runtimeAction === "install-python" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "install-python" ? t("settings.runtime.installing") : t("settings.runtime.installRequirements")}</button>}
        {runtimeStatus?.python.managedInstalled && <button onClick={onDeletePython} disabled={busy}>{runtimeAction === "delete-python" ? <LoadingDots /> : <Trash2 size={16} />}{runtimeAction === "delete-python" ? t("settings.runtime.deleting") : t("settings.runtime.deleteVenv")}</button>}
      </div>
      {runtimeFeedback?.section === "python" && <RuntimeFeedback feedback={runtimeFeedback} />}
      {runtimeStatus?.python.ready && runtimeStatus.python.source !== "managed" && !runtimeStatus.python.requirementsInstalled && <div className="disabled-field">{t("settings.runtime.externalMissing")}</div>}
      {runtimeStatus?.python.error && <div className="disabled-field">{runtimeStatus.python.error}</div>}
    </SetupSection>
    <SetupSection title="FFmpeg" detail={runtimeStatus?.ffmpeg.ready ? `${runtimeStatus.ffmpeg.source} · ${runtimeStatus.ffmpeg.version}` : t("settings.runtime.ffmpegInstall")} ready={Boolean(runtimeStatus?.ffmpeg.ready)} expanded={expansion.ffmpeg} onToggle={() => onToggle("ffmpeg")}>
      <RuntimeLine label={t("settings.runtime.active")} value={runtimeStatus?.ffmpeg.ready ? `${runtimeSourceText(runtimeStatus.ffmpeg.source, t)} · ${runtimeStatus.ffmpeg.version}` : t("settings.runtime.notFound")} ok={Boolean(runtimeStatus?.ffmpeg.ready)} />
      <RuntimeLine label="ffmpeg" value={runtimeStatus?.ffmpeg.ffmpegPath || t("settings.runtime.notFound")} ok={Boolean(runtimeStatus?.ffmpeg.ready)} />
      <RuntimeLine label="ffprobe" value={runtimeStatus?.ffmpeg.ffprobePath || t("settings.runtime.notFound")} ok={Boolean(runtimeStatus?.ffmpeg.ready)} />
      <div className="runtime-actions">
        <button onClick={() => onRefresh("refresh-ffmpeg", "ffmpeg")} disabled={busy}>{runtimeAction === "refresh-ffmpeg" ? <LoadingDots /> : <RefreshCw size={16} />}{runtimeAction === "refresh-ffmpeg" ? t("settings.runtime.refreshing") : t("settings.runtime.refreshFfmpeg")}</button>
        {!runtimeStatus?.ffmpeg.ready && <button onClick={onDownloadFfmpeg} disabled={busy}>{runtimeAction === "download-ffmpeg" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "download-ffmpeg" ? t("settings.runtime.downloading") : t("settings.runtime.downloadFfmpeg")}</button>}
        {runtimeStatus?.ffmpeg.managedInstalled && <button onClick={onDeleteFfmpeg} disabled={busy}>{runtimeAction === "delete-ffmpeg" ? <LoadingDots /> : <Trash2 size={16} />}{runtimeAction === "delete-ffmpeg" ? t("settings.runtime.deleting") : t("settings.runtime.deleteFfmpeg")}</button>}
      </div>
      {runtimeFeedback?.section === "ffmpeg" && <RuntimeFeedback feedback={runtimeFeedback} />}
      {runtimeStatus?.ffmpeg.error && <div className="disabled-field">{runtimeStatus.ffmpeg.error}</div>}
    </SetupSection>
    <SetupSection title={t("settings.runtime.webMedia")} detail={runtimeStatus?.ytDlp.ready ? t("settings.runtime.managedNightly", { version: runtimeStatus.ytDlp.version }) : t("settings.runtime.installDownloader")} ready={Boolean(runtimeStatus?.ytDlp.ready)} expanded={expansion.ytDlp} onToggle={() => onToggle("ytDlp")}>
      <RuntimeLine label="yt-dlp" value={runtimeStatus?.ytDlp.executablePath || t("common.notInstalled")} ok={Boolean(runtimeStatus?.ytDlp.ready)} />
      <div className="runtime-actions">
        <button onClick={() => onRefresh("refresh-ytdlp", "ytDlp")} disabled={busy}>{runtimeAction === "refresh-ytdlp" ? <LoadingDots /> : <RefreshCw size={16} />}{t("settings.runtime.refreshStatus")}</button>
        <button onClick={onInstallOrUpdateYtDlp} disabled={busy}>{runtimeAction === "update-ytdlp" ? <LoadingDots /> : <Download size={16} />}{runtimeStatus?.ytDlp.managedInstalled ? t("settings.runtime.updateNightly") : t("settings.runtime.installYtdlp")}</button>
        {runtimeStatus?.ytDlp.managedInstalled && <button onClick={onDeleteYtDlp} disabled={busy}>{runtimeAction === "delete-ytdlp" ? <LoadingDots /> : <Trash2 size={16} />}{t("settings.runtime.deleteYtdlp")}</button>}
      </div>
      <label>
        <span className="path-label"><TooltipLabel text={t("settings.runtime.denoHelp")}>{t("settings.runtime.deno")}</TooltipLabel></span>
        <div className="row"><input value={ytDlpDenoPath} placeholder={t("settings.runtime.denoPlaceholder")} onChange={(event) => onYtDlpDenoPath(event.target.value)} /><button className="icon-button" aria-label={t("settings.runtime.chooseDeno")} onClick={pickDeno} title={t("settings.runtime.chooseDeno")}><FolderOpen size={17} /></button></div>
      </label>
      <div className="grid2">
        <label>{t("settings.runtime.cookies")}
          <select value={ytDlpCookiesBrowser} onChange={(event) => onYtDlpCookiesBrowser(event.target.value as CookiesBrowser)}>
            <option value="">{t("settings.runtime.noCookies")}</option>
            {["brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"].map((browser) => <option value={browser} key={browser}>{browser[0].toUpperCase() + browser.slice(1)}</option>)}
          </select>
        </label>
        <label>{t("settings.runtime.browserProfile")}
          <input value={ytDlpCookiesProfile} disabled={!ytDlpCookiesBrowser} placeholder={t("settings.runtime.profilePlaceholder")} onChange={(event) => onYtDlpCookiesProfile(event.target.value)} />
        </label>
      </div>
      <small>{t("settings.runtime.cookieNote")}</small>
      {runtimeFeedback?.section === "ytDlp" && <RuntimeFeedback feedback={runtimeFeedback} />}
      {runtimeStatus?.ytDlp.error && <div className="disabled-field">{runtimeStatus.ytDlp.error}</div>}
    </SetupSection>
    <SetupSection title={t("settings.runtime.alignment")} detail={t("settings.runtime.alignmentDetail")} ready={alignmentReady} status={alignmentStatus} expanded={expansion.alignment} onToggle={() => onToggle("alignment")}>
      <div className="runtime-actions">
        {(!runtimeStatus?.alignment.installed || settings.alignment?.model !== runtimeStatus.alignment.modelPath || !settings.alignment?.offlineModelCache) && <button onClick={onDownloadAlignment} disabled={busy || !runtimeStatus?.python.requirementsInstalled}>{runtimeAction === "download-alignment" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "download-alignment" ? t("settings.runtime.preparing") : runtimeStatus?.alignment.installed ? t("settings.runtime.useModel") : t("settings.runtime.downloadAlignment")}</button>}
        {runtimeStatus?.alignment.installed && <button onClick={onDeleteAlignment} disabled={busy}>{runtimeAction === "delete-alignment" ? <LoadingDots /> : <Trash2 size={16} />}{runtimeAction === "delete-alignment" ? t("settings.runtime.deleting") : t("settings.runtime.deleteModel")}</button>}
      </div>
    </SetupSection>
  </>;
}

function pythonRuntimeSummary(status: RuntimeSetupStatus | null, t: T): string { if (!status) return t("settings.runtime.checking"); if (!status.python.ready) return t("settings.runtime.pythonRequired"); if (!status.python.requirementsInstalled) return t("settings.runtime.depsMissing"); return `${runtimeSourceText(status.python.source, t)} · ${status.python.version}`; }
function runtimeSourceText(source: "selected" | "managed" | "path" | "missing", t: T): string { return source === "selected" ? t("settings.runtime.manual") : source === "managed" ? t("settings.runtime.managed") : source === "path" ? t("settings.runtime.systemPath") : t("settings.runtime.missing"); }
function LoadingDots() { return <span className="loading-dots" aria-hidden="true"><i /><i /><i /></span>; }
function RuntimeLine({ label, value, ok }: { label: string; value: string; ok: boolean }) { return <div className="runtime-line"><span className={ok ? "mini-ok" : "mini-warn"}>{ok ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}</span><strong>{label}</strong><small>{value}</small></div>; }
function RuntimeFeedback({ feedback }: { feedback: { text: string; ok: boolean } }) { return <div className={feedback.ok ? "runtime-feedback ok" : "runtime-feedback error"}>{feedback.ok ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}<span>{feedback.text}</span></div>; }
function PathInput({ value, onChange, onPick }: { value: string; onChange(value: string): void; onPick(): void }) { const { t } = useI18n(); return <label><span className="path-label"><TooltipLabel text={t("settings.runtime.manualOverrideHelp")}>{t("settings.runtime.manualOverride")}</TooltipLabel></span><div className="row"><input value={value} placeholder={t("settings.runtime.manualPlaceholder")} onChange={(event) => onChange(event.target.value)} /><button className="icon-button" aria-label={t("settings.runtime.choosePython")} onClick={onPick} title={t("settings.runtime.choosePython")}><FolderOpen size={17} /></button></div></label>; }
