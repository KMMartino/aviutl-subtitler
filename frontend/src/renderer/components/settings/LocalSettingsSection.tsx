import { AlertTriangle, Brain, CheckCircle, CircleGauge, Download, FolderOpen, Info, Trash2 } from "lucide-react";
import type {
  CoreWorkflowSettings,
  CurrentLlamaServerState,
  HuggingFaceDownloaderStatus,
  LlamaBackendId,
  LlamaBackendOption,
  LlamaReleaseCheck,
  LocalModelProfile,
  LocalModelStatus,
  ManagedLlamaStatus,
  PathStatus,
} from "../../lib/types";
import type { SettingsExpansion } from "../../lib/settingsExpansion";
import TooltipLabel from "../TooltipLabel";
import SetupSection from "./SetupSection";
import { useI18n } from "../../i18n";

type Props = {
  settings: CoreWorkflowSettings;
  pathStatus: Record<string, PathStatus>;
  modelsDirectory: string;
  localModelStatus: LocalModelStatus | null;
  localProfiles: LocalModelProfile[];
  localProfileStatuses: Record<string, LocalModelStatus>;
  selectedLocalProfile: string;
  downloadingModels: boolean;
  deletingManaged: string;
  modelDownloadMode: "direct" | "huggingface";
  hfDownloaderStatus: HuggingFaceDownloaderStatus | null;
  installingHfDownloader: boolean;
  llamaBackends: LlamaBackendOption[];
  selectedLlamaBackend: LlamaBackendId;
  llamaRelease: LlamaReleaseCheck | null;
  managedLlamaStatus: ManagedLlamaStatus | null;
  currentLlamaState: CurrentLlamaServerState | null;
  downloadingLlama: boolean;
  expansion: SettingsExpansion;
  onToggleExpansion(section: keyof SettingsExpansion): void;
  onChange(settings: CoreWorkflowSettings): void;
  onModelsDirectory(path: string): void;
  onDownloadLocalModels(): void;
  onDeleteLocalModels(): void;
  onModelDownloadMode(mode: "direct" | "huggingface"): void;
  onInstallHfDownloader(): void;
  onLocalProfile(profile: string): void;
  onLlamaBackend(value: LlamaBackendId): void;
  onCheckLlamaRelease(): void;
  onDownloadLlama(): void;
  onDeleteLlama(): void;
  onUseManagedLlama(path: string): void;
  onRevertManagedLlama(path: string): void;
};

export default function LocalSettingsSection({ settings, pathStatus, modelsDirectory, localModelStatus, localProfiles, localProfileStatuses, selectedLocalProfile, downloadingModels, deletingManaged, modelDownloadMode, hfDownloaderStatus, installingHfDownloader, llamaBackends, selectedLlamaBackend, llamaRelease, managedLlamaStatus, currentLlamaState, downloadingLlama, expansion, onToggleExpansion, onChange, onModelsDirectory, onDownloadLocalModels, onDeleteLocalModels, onModelDownloadMode, onInstallHfDownloader, onLocalProfile, onLlamaBackend, onCheckLlamaRelease, onDownloadLlama, onDeleteLlama, onUseManagedLlama, onRevertManagedLlama }: Props) {
  const { t } = useI18n();
  const local = settings.local ?? { model: "", mmproj: "", llamaServer: "", cleanupModel: "", cleanupLlamaServer: "", transcriptionDraftModel: "", cleanupDraftModel: "" };
  const anyLocalProfileInstalled = Object.values(localProfileStatuses).some((status) => status.installed);
  const selectedLocalProfileInstalled = Boolean(localModelStatus?.installed);
  const hfPythonReady = Boolean(hfDownloaderStatus?.pythonReady);
  const hfReady = Boolean(hfDownloaderStatus?.ready);

  async function pickPath(callback: (path: string) => void) {
    const path = await window.subtitler.chooseExecutable();
    if (path) callback(path);
  }

  return (
    <div className="local-setup">
      {!anyLocalProfileInstalled && (
        <div className="local-blocking-alert" role="alert">
          <AlertTriangle size={22} />
          <span><strong>{t("settings.local.noneTitle")}</strong><small>{t("settings.local.noneDetail")}</small></span>
        </div>
      )}
      <SetupSection
        title={t("settings.local.model")}
        detail={`${selectedProfile(localProfiles, selectedLocalProfile)?.label ?? t("settings.local.loadingProfile")} · ${selectedProfile(localProfiles, selectedLocalProfile)?.summary ?? ""}`}
        ready={selectedLocalProfileInstalled}
        expanded={expansion.localModel}
        onToggle={() => onToggleExpansion("localModel")}
      >
        <label>
          <TooltipLabel text={t("settings.local.profileHelp")}>{t("settings.local.profile")}</TooltipLabel>
          <select value={selectedLocalProfile} onChange={(event) => onLocalProfile(event.target.value)}>
            <optgroup label={t("settings.local.standard")}>
              {localProfiles.filter((profile) => !profile.experimental).map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
            </optgroup>
            <optgroup label={t("settings.local.experimentalMtp")}>
              {localProfiles.filter((profile) => profile.experimental).map((profile) => <option key={profile.id} value={profile.id}>{profile.label} - {t("settings.local.experimental")}</option>)}
            </optgroup>
          </select>
        </label>
        <div className="local-profile">
          <div className="local-profile-title">
            <span><strong>{selectedProfile(localProfiles, selectedLocalProfile)?.label ?? t("settings.local.gemmaProfile")}</strong><small>{selectedProfile(localProfiles, selectedLocalProfile)?.summary}</small></span>
            <span className={localModelStatus?.installed ? "env-ok" : "env-missing"}>
              {localModelStatus?.installed ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}
              {localModelStatus?.installed ? t("common.installed") : t("common.notInstalled")}
            </span>
          </div>
          <div className="profile-traits">
            <span><Brain size={15} /> {selectedProfile(localProfiles, selectedLocalProfile)?.experimental ? t("settings.local.experimentalSpeed") : t("settings.local.balancedQuality")}</span>
            <span><CircleGauge size={15} /> {selectedProfile(localProfiles, selectedLocalProfile)?.vramGb ?? "?"} GB VRAM</span>
            <span className="tooltip" tabIndex={0}><Info size={15} /><span className="tooltip-content">{localProfileBlurb(selectedLocalProfile, t)}</span></span>
          </div>
          <label>
            <TooltipLabel text={t("settings.local.downloadMethodHelp")}>{t("settings.local.downloadMethod")}</TooltipLabel>
            <select value={modelDownloadMode} onChange={(event) => onModelDownloadMode(event.target.value as "direct" | "huggingface")}>
              <option value="direct">{t("settings.local.basicHttp")}</option>
              <option value="huggingface">{t("settings.local.pythonHf")}</option>
            </select>
          </label>
          {modelDownloadMode === "huggingface" && (
            <>
              <RuntimeLine label="Python" value={hfPythonReady ? runtimeSourceLabel(hfDownloaderStatus, t) : t("settings.local.required")} ok={hfPythonReady} />
              <RuntimeLine label={t("settings.local.hfPackages")} value={hfReady ? t("settings.local.readyWithXet", { suffix: hfDownloaderStatus?.xetReady ? " with hf_xet" : "" }) : hfPythonReady ? t("settings.local.installEnable") : t("settings.local.needsPython")} ok={hfReady} />
              {!hfReady && <div className="disabled-field">{hfPythonReady ? t("settings.local.installFast") : t("settings.local.createPythonFast")}</div>}
              <button onClick={onInstallHfDownloader} disabled={hfReady || !hfPythonReady || installingHfDownloader || downloadingModels}>
                {installingHfDownloader ? <LoadingDots /> : <Download size={16} />}
                {installingHfDownloader ? t("settings.local.installingPackages") : hfReady ? t("settings.local.packagesInstalled") : t("settings.local.installPackages")}
              </button>
            </>
          )}
          <div className="button-row">
            <button onClick={onDownloadLocalModels} disabled={downloadingModels || deletingManaged === "models" || localModelStatus?.installed || (!localModelStatus?.needsVerification && modelDownloadMode === "huggingface" && !hfDownloaderStatus?.ready)}>
              {downloadingModels ? <LoadingDots /> : <Download size={16} />}
              {downloadingModels ? localModelStatus?.needsVerification ? t("settings.local.verifyingFiles") : t("settings.local.downloadingModels") : localModelStatus?.installed ? t("settings.local.modelsInstalled") : localModelStatus?.needsVerification ? t("settings.local.verifyFiles") : t("settings.local.downloadProfile")}
            </button>
            <button onClick={onDeleteLocalModels} disabled={downloadingModels || deletingManaged === "models" || !localModelStatus?.installed || !localModelStatus?.managed}>
              {deletingManaged === "models" ? <LoadingDots /> : <Trash2 size={16} />}
              {deletingManaged === "models" ? t("settings.runtime.deleting") : t("settings.local.deleteFiles")}
            </button>
          </div>
          <div className="managed-server-note">{t("settings.local.downloadNote")}</div>
        </div>
        <label>
          <TooltipLabel text={t("settings.local.modelsDirectoryHelp")}>{t("settings.local.modelsDirectory")}</TooltipLabel>
          <div className="row">
            <input value={modelsDirectory} onChange={(event) => onModelsDirectory(event.target.value)} />
            <button className="icon-button" aria-label={t("settings.local.chooseModelsDirectory")} onClick={async () => {
              const selected = await window.subtitler.chooseDirectory();
              if (selected) onModelsDirectory(selected);
            }} title={t("settings.local.chooseModelsDirectory")}><FolderOpen size={17} /></button>
          </div>
        </label>
        {localModelStatus && <div className="managed-files">
          <ManagedFile label={t("settings.local.transcription")} file={localModelStatus.files.transcription} />
          <ManagedFile label={t("settings.local.projector")} file={localModelStatus.files.projector} />
          <ManagedFile label={t("settings.local.cleanup")} file={localModelStatus.files.cleanup} />
          {localModelStatus.files.transcriptionDraft && <ManagedFile label={`${t("settings.local.transcription")} MTP`} file={localModelStatus.files.transcriptionDraft} />}
          {localModelStatus.files.cleanupDraft && <ManagedFile label={`${t("settings.local.cleanup")} MTP`} file={localModelStatus.files.cleanupDraft} />}
        </div>}
      </SetupSection>
      <SetupSection
        title={t("settings.local.serverBackend")}
        detail={serverSummary(currentLlamaState, pathStatus.llamaServer?.exists, t)}
        ready={Boolean(pathStatus.llamaServer?.exists)}
        expanded={expansion.server}
        onToggle={() => onToggleExpansion("server")}
      >
        <PathInput label="llama-server" tip={t("settings.local.serverHelp")} value={local.llamaServer} status={pathStatus.llamaServer} onChange={(value) => {
          onChange({ ...settings, local: { ...local, llamaServer: value, cleanupLlamaServer: value } });
        }} onPick={() => pickPath((value) => onChange({ ...settings, local: { ...local, llamaServer: value, cleanupLlamaServer: value } }))} />
        <ManagedServerInstall backends={llamaBackends} selectedBackend={selectedLlamaBackend} release={llamaRelease} status={managedLlamaStatus} currentState={currentLlamaState} downloading={downloadingLlama} deleting={deletingManaged === "llama"} currentServerValid={Boolean(pathStatus.llamaServer?.exists)} onBackend={onLlamaBackend} onCheck={onCheckLlamaRelease} onDownload={onDownloadLlama} onDelete={onDeleteLlama} onUse={onUseManagedLlama} onRevert={onRevertManagedLlama} />
      </SetupSection>
    </div>
  );
}

function selectedProfile(profiles: LocalModelProfile[], id: string) { return profiles.find((profile) => profile.id === id); }

function localProfileBlurb(id: string, t: ReturnType<typeof useI18n>["t"]): string {
  if (id.endsWith("-mtp")) return t("settings.local.profileMtpBlurb");
  if (id === "8gb-gpu-gemma") return t("settings.local.profile8GbBlurb");
  if (id === "12gb-gpu-gemma") return t("settings.local.profile12GbBlurb");
  return t("settings.local.profile16GbBlurb");
}

function runtimeSourceLabel(status: HuggingFaceDownloaderStatus | null, t: ReturnType<typeof useI18n>["t"]): string {
  if (!status?.pythonReady) return t("settings.local.required");
  if (status.pythonSource === "selected") return t("settings.local.selectedRuntime");
  if (status.pythonSource === "managed") return t("settings.local.managedRuntime");
  return t("settings.local.pythonOnPath");
}

function LoadingDots() { return <span className="loading-dots" aria-hidden="true"><i /><i /><i /></span>; }

function ManagedFile({ label, file }: { label: string; file: { path: string; exists: boolean } }) {
  return <div title={file.path}>{file.exists ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}<span>{label}</span></div>;
}

function RuntimeLine({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return <div className="runtime-line">{ok ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}<span>{label}</span><small title={value}>{value}</small></div>;
}

function ManagedServerInstall({ backends, selectedBackend, release, status, currentState, downloading, deleting, currentServerValid, onBackend, onCheck, onDownload, onDelete, onUse, onRevert }: {
  backends: LlamaBackendOption[]; selectedBackend: LlamaBackendId; release: LlamaReleaseCheck | null; status: ManagedLlamaStatus | null; currentState: CurrentLlamaServerState | null; downloading: boolean; deleting: boolean; currentServerValid: boolean;
  onBackend(value: LlamaBackendId): void; onCheck(): void; onDownload(): void; onDelete(): void; onUse(path: string): void; onRevert(path: string): void;
}) {
  const { t } = useI18n();
  const selected = backends.find((backend) => backend.id === selectedBackend);
  const matchedAsset = release?.assets.find((asset) => asset.backend === selectedBackend);
  const selectedIsCurrent = Boolean(status?.installed && currentState?.managed && status.serverPath === currentState.serverPath);
  return (
    <div className="managed-server">
      <div className="managed-server-title">
        <strong>{t("settings.local.managedInstall")}</strong>
        <span className={status?.installed ? "env-ok" : "env-missing"}>{status?.installed ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}{status?.installed ? t("common.installed") : t("common.notInstalled")}</span>
      </div>
      <label>
        <TooltipLabel text={t("settings.local.backendHelp")}>{t("settings.local.backend")}</TooltipLabel>
        <select value={selectedBackend} onChange={(event) => onBackend(event.target.value as LlamaBackendId)}>{backends.map((backend) => <option key={backend.id} value={backend.id}>{backend.label}</option>)}</select>
      </label>
      <div className="managed-server-note">{selected ? backendDescription(selected, t) : ""}</div>
      <div className="server-facts">
        <span><strong>{t("settings.local.current")}</strong><small>{currentServerLabel(currentState, currentServerValid, t)}</small></span>
        <span><strong>{t("settings.local.currentVersion")}</strong><small>{currentState?.version ? currentState.version.split(/\r?\n/)[0] : t("common.unknown")}</small></span>
        <span><strong>{t("settings.local.latestRelease")}</strong><small>{release?.releaseTag ?? t("settings.local.notChecked")}</small></span>
        <span><strong>{t("settings.local.asset")}</strong><small>{matchedAsset?.assetName ?? t("settings.local.notChecked")}</small></span>
        <span><strong>{t("common.installed")}</strong><small>{status?.installed ? status.serverPath : t("common.notInstalled")}</small></span>
        {status?.version && <span><strong>{t("settings.local.version")}</strong><small>{status.version.split(/\r?\n/)[0]}</small></span>}
        {currentState?.previous && <span><strong>{t("settings.local.previous")}</strong><small>{currentState.previous.releaseTag}</small></span>}
      </div>
      <div className={currentServerValid ? "server-advice info" : "server-advice warn"}>{serverAdvice(currentState, currentServerValid, selectedIsCurrent, t)}</div>
      <div className="button-row">
        <button type="button" onClick={onCheck} disabled={deleting}>{t("settings.local.checkLatest")}</button>
        <button type="button" onClick={onDownload} disabled={downloading || deleting}>{downloading ? <LoadingDots /> : <Download size={16} />}{downloading ? t("settings.local.downloadingServer") : t("settings.local.downloadServer")}</button>
        <button type="button" onClick={onDelete} disabled={downloading || deleting || !status?.installed}>{deleting ? <LoadingDots /> : <Trash2 size={16} />}{deleting ? t("settings.runtime.deleting") : t("settings.local.deleteServer")}</button>
        <button type="button" className={!currentServerValid && status?.installed ? "primary-inline" : ""} disabled={deleting || !status?.installed || selectedIsCurrent} onClick={() => status?.serverPath && onUse(status.serverPath)}>{selectedIsCurrent ? t("settings.local.serverActive") : t("settings.local.useServer")}</button>
        <button type="button" disabled={deleting || !currentState?.previous?.installed} onClick={() => currentState?.previous?.serverPath && onRevert(currentState.previous.serverPath)}>{t("settings.local.revertServer")}</button>
      </div>
    </div>
  );
}

function serverSummary(state: CurrentLlamaServerState | null, pathExists: boolean | undefined, t: ReturnType<typeof useI18n>["t"]): string {
  if (!pathExists) return t("settings.local.chooseServer");
  if (state?.managed) return `Managed ${state.backend} ${state.releaseTag || ""}`.trim();
  return t("settings.local.manualReady");
}

function currentServerLabel(state: CurrentLlamaServerState | null, valid: boolean, t: ReturnType<typeof useI18n>["t"]): string {
  if (!valid) return t("common.notReady");
  if (state?.managed) return `Managed ${state.backend} ${state.releaseTag || ""}`.trim();
  return t("settings.local.manualServer");
}

function serverAdvice(state: CurrentLlamaServerState | null, valid: boolean, selectedIsCurrent: boolean, t: ReturnType<typeof useI18n>["t"]): string {
  if (!valid) return t("settings.local.noValidServer");
  if (state?.managed) return selectedIsCurrent ? t("settings.local.managedCurrent") : t("settings.local.managedSwitch");
  return t("settings.local.manualAdvice");
}

function backendDescription(backend: LlamaBackendOption, t: ReturnType<typeof useI18n>["t"]): string {
  if (backend.id === "vulkan") return t("settings.local.backendVulkan");
  if (backend.id === "cuda-12") return t("settings.local.backendCuda");
  return backend.description;
}

function PathInput({ label, tip, value, status, onChange, onPick }: { label: string; tip: string; value: string; status?: PathStatus; onChange(value: string): void; onPick(): void }) {
  const { t } = useI18n();
  return (
    <label>
      <span className="path-label"><TooltipLabel text={tip}>{label}</TooltipLabel><span className={status?.exists ? "mini-ok" : "mini-warn"} title={status?.exists ? t("settings.local.fileFound") : t("settings.local.fileMissing")}>{status?.exists ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}</span></span>
      <div className="row"><input value={value} onChange={(event) => onChange(event.target.value)} /><button className="icon-button" aria-label={t("settings.local.choosePath", { label })} onClick={onPick} title={t("settings.local.choosePath", { label })}><FolderOpen size={17} /></button></div>
    </label>
  );
}
