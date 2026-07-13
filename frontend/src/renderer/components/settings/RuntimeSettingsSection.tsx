import { AlertTriangle, CheckCircle, Download, FolderOpen, RefreshCw, Trash2 } from "lucide-react";
import type { CoreWorkflowSettings, RuntimeSetupStatus } from "../../lib/types";
import type { SettingsExpansion } from "../../lib/settingsExpansion";
import SetupSection, { type SetupStatus } from "./SetupSection";
import TooltipLabel from "../TooltipLabel";

type Feedback = { section: "python" | "ffmpeg" | "alignment"; text: string; ok: boolean } | null;

type Props = {
  settings: CoreWorkflowSettings;
  pythonPath: string;
  pythonReady: boolean;
  runtimeStatus: RuntimeSetupStatus | null;
  runtimeAction: string;
  runtimeFeedback: Feedback;
  expansion: SettingsExpansion;
  onToggle(section: "python" | "ffmpeg" | "alignment"): void;
  onPythonPath(path: string): void;
  onRefresh(action?: string, feedbackSection?: "python" | "ffmpeg" | "alignment"): void;
  onCreatePython(): void;
  onInstallPythonRequirements(): void;
  onDeletePython(): void;
  onDownloadFfmpeg(): void;
  onDeleteFfmpeg(): void;
  onDownloadAlignment(): void;
  onDeleteAlignment(): void;
};

export default function RuntimeSettingsSection({ settings, pythonPath, pythonReady, runtimeStatus, runtimeAction, runtimeFeedback, expansion, onToggle, onPythonPath, onRefresh, onCreatePython, onInstallPythonRequirements, onDeletePython, onDownloadFfmpeg, onDeleteFfmpeg, onDownloadAlignment, onDeleteAlignment }: Props) {
  const busy = Boolean(runtimeAction);
  const alignmentDownloaded = Boolean(runtimeStatus?.alignment.installed);
  const alignmentReady = Boolean(alignmentDownloaded && runtimeStatus?.alignment.modelPath && settings.alignment?.model === runtimeStatus.alignment.modelPath && settings.alignment?.offlineModelCache);
  const alignmentStatus: SetupStatus = alignmentReady ? { kind: "ready", label: "Ready" } : alignmentDownloaded ? { kind: "warning", label: "Downloaded" } : { kind: "required", label: "Not installed" };
  async function pickPython() { const path = await window.subtitler.chooseExecutable(); if (path) onPythonPath(path); }

  return <>
    <SetupSection title="Python runtime" detail={pythonRuntimeSummary(runtimeStatus)} ready={pythonReady} expanded={expansion.python} onToggle={() => onToggle("python")}>
      <RuntimeLine label="Active" value={runtimeStatus?.python.ready ? `${runtimeSourceText(runtimeStatus.python.source)} · ${runtimeStatus.python.version}` : "Not found"} ok={Boolean(runtimeStatus?.python.ready)} />
      <RuntimeLine label="Path" value={runtimeStatus?.python.resolvedPath || "No usable Python found"} ok={Boolean(runtimeStatus?.python.ready)} />
      <RuntimeLine label="App deps" value={runtimeStatus?.python.requirementsInstalled ? "Installed" : "Missing ctc_forced_aligner"} ok={Boolean(runtimeStatus?.python.requirementsInstalled)} />
      <PathInput value={pythonPath} onChange={onPythonPath} onPick={pickPython} />
      {pythonPath && <div className="runtime-actions"><button onClick={() => onPythonPath("")} disabled={busy}>Use auto runtime</button></div>}
      <div className="runtime-actions">
        <button onClick={() => onRefresh("refresh-python", "python")} disabled={busy}>{runtimeAction === "refresh-python" ? <LoadingDots /> : <RefreshCw size={16} />}{runtimeAction === "refresh-python" ? "Refreshing..." : "Refresh runtime"}</button>
        {!runtimeStatus?.python.managedInstalled && <button onClick={onCreatePython} disabled={busy}>{runtimeAction === "create-python" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "create-python" ? "Creating venv..." : "Create managed venv"}</button>}
        {runtimeStatus?.python.source === "managed" && !runtimeStatus.python.requirementsInstalled && <button onClick={onInstallPythonRequirements} disabled={busy || !runtimeStatus.python.ready}>{runtimeAction === "install-python" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "install-python" ? "Installing..." : "Install managed requirements"}</button>}
        {runtimeStatus?.python.managedInstalled && <button onClick={onDeletePython} disabled={busy}>{runtimeAction === "delete-python" ? <LoadingDots /> : <Trash2 size={16} />}{runtimeAction === "delete-python" ? "Deleting..." : "Delete managed venv"}</button>}
      </div>
      {runtimeFeedback?.section === "python" && <RuntimeFeedback feedback={runtimeFeedback} />}
      {runtimeStatus?.python.ready && runtimeStatus.python.source !== "managed" && !runtimeStatus.python.requirementsInstalled && <div className="disabled-field">The active Python is outside the managed venv and is missing app requirements. Install them in that environment, or clear the override to use the managed venv.</div>}
      {runtimeStatus?.python.error && <div className="disabled-field">{runtimeStatus.python.error}</div>}
    </SetupSection>
    <SetupSection title="FFmpeg" detail={runtimeStatus?.ffmpeg.ready ? `${runtimeStatus.ffmpeg.source} · ${runtimeStatus.ffmpeg.version}` : "Install FFmpeg or use managed download"} ready={Boolean(runtimeStatus?.ffmpeg.ready)} expanded={expansion.ffmpeg} onToggle={() => onToggle("ffmpeg")}>
      <RuntimeLine label="Active" value={runtimeStatus?.ffmpeg.ready ? `${runtimeSourceText(runtimeStatus.ffmpeg.source)} · ${runtimeStatus.ffmpeg.version}` : "Not found"} ok={Boolean(runtimeStatus?.ffmpeg.ready)} />
      <RuntimeLine label="ffmpeg" value={runtimeStatus?.ffmpeg.ffmpegPath || "Not found"} ok={Boolean(runtimeStatus?.ffmpeg.ready)} />
      <RuntimeLine label="ffprobe" value={runtimeStatus?.ffmpeg.ffprobePath || "Not found"} ok={Boolean(runtimeStatus?.ffmpeg.ready)} />
      <div className="runtime-actions">
        <button onClick={() => onRefresh("refresh-ffmpeg", "ffmpeg")} disabled={busy}>{runtimeAction === "refresh-ffmpeg" ? <LoadingDots /> : <RefreshCw size={16} />}{runtimeAction === "refresh-ffmpeg" ? "Refreshing..." : "Refresh FFmpeg"}</button>
        {!runtimeStatus?.ffmpeg.ready && <button onClick={onDownloadFfmpeg} disabled={busy}>{runtimeAction === "download-ffmpeg" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "download-ffmpeg" ? "Downloading..." : "Download managed FFmpeg"}</button>}
        {runtimeStatus?.ffmpeg.managedInstalled && <button onClick={onDeleteFfmpeg} disabled={busy}>{runtimeAction === "delete-ffmpeg" ? <LoadingDots /> : <Trash2 size={16} />}{runtimeAction === "delete-ffmpeg" ? "Deleting..." : "Delete managed FFmpeg"}</button>}
      </div>
      {runtimeFeedback?.section === "ffmpeg" && <RuntimeFeedback feedback={runtimeFeedback} />}
      {runtimeStatus?.ffmpeg.error && <div className="disabled-field">{runtimeStatus.ffmpeg.error}</div>}
    </SetupSection>
    <SetupSection title="Alignment model" detail="Required for subtitle timing · 1.18 GiB" ready={alignmentReady} status={alignmentStatus} expanded={expansion.alignment} onToggle={() => onToggle("alignment")}>
      <div className="runtime-actions">
        {(!runtimeStatus?.alignment.installed || settings.alignment?.model !== runtimeStatus.alignment.modelPath || !settings.alignment?.offlineModelCache) && <button onClick={onDownloadAlignment} disabled={busy || !runtimeStatus?.python.requirementsInstalled}>{runtimeAction === "download-alignment" ? <LoadingDots /> : <Download size={16} />}{runtimeAction === "download-alignment" ? "Preparing..." : runtimeStatus?.alignment.installed ? "Use model" : "Download alignment model"}</button>}
        {runtimeStatus?.alignment.installed && <button onClick={onDeleteAlignment} disabled={busy}>{runtimeAction === "delete-alignment" ? <LoadingDots /> : <Trash2 size={16} />}{runtimeAction === "delete-alignment" ? "Deleting..." : "Delete managed model"}</button>}
      </div>
    </SetupSection>
  </>;
}

function pythonRuntimeSummary(status: RuntimeSetupStatus | null): string { if (!status) return "Checking runtime"; if (!status.python.ready) return "Python is required"; if (!status.python.requirementsInstalled) return "Python found · app dependencies missing"; return `${runtimeSourceText(status.python.source)} · ${status.python.version}`; }
function runtimeSourceText(source: "selected" | "managed" | "path" | "missing"): string { return source === "selected" ? "Manual" : source === "managed" ? "Managed" : source === "path" ? "System PATH" : "Missing"; }
function LoadingDots() { return <span className="loading-dots" aria-hidden="true"><i /><i /><i /></span>; }
function RuntimeLine({ label, value, ok }: { label: string; value: string; ok: boolean }) { return <div className="runtime-line"><span className={ok ? "mini-ok" : "mini-warn"}>{ok ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}</span><strong>{label}</strong><small>{value}</small></div>; }
function RuntimeFeedback({ feedback }: { feedback: { text: string; ok: boolean } }) { return <div className={feedback.ok ? "runtime-feedback ok" : "runtime-feedback error"}>{feedback.ok ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}<span>{feedback.text}</span></div>; }
function PathInput({ value, onChange, onPick }: { value: string; onChange(value: string): void; onPick(): void }) { return <label><span className="path-label"><TooltipLabel text="Leave empty for auto mode: the app uses its managed venv when available, otherwise Python on PATH. Set this only to force a specific Python executable.">Manual Python override</TooltipLabel></span><div className="row"><input value={value} placeholder="Auto: managed venv, then python on PATH" onChange={(event) => onChange(event.target.value)} /><button className="icon-button" aria-label="Choose Manual Python override" onClick={onPick} title="Choose Manual Python override"><FolderOpen size={17} /></button></div></label>; }
