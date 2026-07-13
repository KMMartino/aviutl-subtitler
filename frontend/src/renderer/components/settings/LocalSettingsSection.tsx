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
          <span><strong>No local models installed</strong><small>Local generation is unavailable. Choose a GPU profile and download its models to continue.</small></span>
        </div>
      )}
      <SetupSection
        title="Local model"
        detail={`${selectedProfile(localProfiles, selectedLocalProfile)?.label ?? "Loading profile"} · ${selectedProfile(localProfiles, selectedLocalProfile)?.summary ?? ""}`}
        ready={selectedLocalProfileInstalled}
        expanded={expansion.localModel}
        onToggle={() => onToggleExpansion("localModel")}
      >
        <label>
          <TooltipLabel text="Choose a profile for the GPU's available VRAM. Each profile leaves capacity for runtime context and uses a matching audio projector.">GPU model profile</TooltipLabel>
          <select value={selectedLocalProfile} onChange={(event) => onLocalProfile(event.target.value)}>
            <optgroup label="Standard">
              {localProfiles.filter((profile) => !profile.experimental).map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
            </optgroup>
            <optgroup label="Experimental MTP">
              {localProfiles.filter((profile) => profile.experimental).map((profile) => <option key={profile.id} value={profile.id}>{profile.label} - Experimental</option>)}
            </optgroup>
          </select>
        </label>
        <div className="local-profile">
          <div className="local-profile-title">
            <span><strong>{selectedProfile(localProfiles, selectedLocalProfile)?.label ?? "Gemma GPU Profile"}</strong><small>{selectedProfile(localProfiles, selectedLocalProfile)?.summary}</small></span>
            <span className={localModelStatus?.installed ? "env-ok" : "env-missing"}>
              {localModelStatus?.installed ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}
              {localModelStatus?.installed ? "Installed" : "Not installed"}
            </span>
          </div>
          <div className="profile-traits">
            <span><Brain size={15} /> {selectedProfile(localProfiles, selectedLocalProfile)?.experimental ? "Experimental faster decoding" : "Balanced local quality"}</span>
            <span><CircleGauge size={15} /> {selectedProfile(localProfiles, selectedLocalProfile)?.vramGb ?? "?"} GB VRAM</span>
            <span className="tooltip" tabIndex={0}><Info size={15} /><span className="tooltip-content">{localProfileBlurb(selectedLocalProfile)}</span></span>
          </div>
          <label>
            <TooltipLabel text="Basic HTTP is always available. The Python Hugging Face downloader can be faster when the Python runtime has huggingface_hub and hf_xet installed.">Download method</TooltipLabel>
            <select value={modelDownloadMode} onChange={(event) => onModelDownloadMode(event.target.value as "direct" | "huggingface")}>
              <option value="direct">Basic HTTP request</option>
              <option value="huggingface">Python HF downloader</option>
            </select>
          </label>
          {modelDownloadMode === "huggingface" && (
            <>
              <RuntimeLine label="Python" value={hfPythonReady ? runtimeSourceLabel(hfDownloaderStatus) : "Required"} ok={hfPythonReady} />
              <RuntimeLine label="HF packages" value={hfReady ? `Ready${hfDownloaderStatus?.xetReady ? " with hf_xet" : ""}` : hfPythonReady ? "Install to enable" : "Needs Python first"} ok={hfReady} />
              {!hfReady && <div className="disabled-field">{hfPythonReady ? "Install downloader packages to enable the faster path." : "Create or select a Python runtime for the faster path."}</div>}
              <button onClick={onInstallHfDownloader} disabled={hfReady || !hfPythonReady || installingHfDownloader || downloadingModels}>
                {installingHfDownloader ? <LoadingDots /> : <Download size={16} />}
                {installingHfDownloader ? "Installing packages..." : hfReady ? "Downloader packages installed" : "Install downloader packages"}
              </button>
            </>
          )}
          <div className="button-row">
            <button onClick={onDownloadLocalModels} disabled={downloadingModels || deletingManaged === "models" || localModelStatus?.installed || (!localModelStatus?.needsVerification && modelDownloadMode === "huggingface" && !hfDownloaderStatus?.ready)}>
              {downloadingModels ? <LoadingDots /> : <Download size={16} />}
              {downloadingModels ? localModelStatus?.needsVerification ? "Verifying files..." : "Downloading models..." : localModelStatus?.installed ? "Models installed" : localModelStatus?.needsVerification ? "Verify existing files" : "Download model profile"}
            </button>
            <button onClick={onDeleteLocalModels} disabled={downloadingModels || deletingManaged === "models" || !localModelStatus?.installed || !localModelStatus?.managed}>
              {deletingManaged === "models" ? <LoadingDots /> : <Trash2 size={16} />}
              {deletingManaged === "models" ? "Deleting..." : "Delete managed files"}
            </button>
          </div>
          <div className="managed-server-note">Download progress appears in the logs on the main panel. Large model downloads can take a long time, especially when Hugging Face is busy; running them overnight may be more practical.</div>
        </div>
        <label>
          <TooltipLabel text="Directory managed by the application for downloaded GGUF models and multimodal projectors.">Models directory</TooltipLabel>
          <div className="row">
            <input value={modelsDirectory} onChange={(event) => onModelsDirectory(event.target.value)} />
            <button className="icon-button" aria-label="Choose models directory" onClick={async () => {
              const selected = await window.subtitler.chooseDirectory();
              if (selected) onModelsDirectory(selected);
            }} title="Choose models directory"><FolderOpen size={17} /></button>
          </div>
        </label>
        {localModelStatus && <div className="managed-files">
          <ManagedFile label="Transcription" file={localModelStatus.files.transcription} />
          <ManagedFile label="Projector" file={localModelStatus.files.projector} />
          <ManagedFile label="Cleanup" file={localModelStatus.files.cleanup} />
          {localModelStatus.files.transcriptionDraft && <ManagedFile label="Transcription MTP" file={localModelStatus.files.transcriptionDraft} />}
          {localModelStatus.files.cleanupDraft && <ManagedFile label="Cleanup MTP" file={localModelStatus.files.cleanupDraft} />}
        </div>}
      </SetupSection>
      <SetupSection
        title="Server backend"
        detail={serverSummary(currentLlamaState, pathStatus.llamaServer?.exists)}
        ready={Boolean(pathStatus.llamaServer?.exists)}
        expanded={expansion.server}
        onToggle={() => onToggleExpansion("server")}
      >
        <PathInput label="llama-server" tip="The llama.cpp server executable for your computer and GPU backend. The same executable is used for transcription and cleanup." value={local.llamaServer} status={pathStatus.llamaServer} onChange={(value) => {
          onChange({ ...settings, local: { ...local, llamaServer: value, cleanupLlamaServer: value } });
        }} onPick={() => pickPath((value) => onChange({ ...settings, local: { ...local, llamaServer: value, cleanupLlamaServer: value } }))} />
        <ManagedServerInstall backends={llamaBackends} selectedBackend={selectedLlamaBackend} release={llamaRelease} status={managedLlamaStatus} currentState={currentLlamaState} downloading={downloadingLlama} deleting={deletingManaged === "llama"} currentServerValid={Boolean(pathStatus.llamaServer?.exists)} onBackend={onLlamaBackend} onCheck={onCheckLlamaRelease} onDownload={onDownloadLlama} onDelete={onDeleteLlama} onUse={onUseManagedLlama} onRevert={onRevertManagedLlama} />
      </SetupSection>
    </div>
  );
}

function selectedProfile(profiles: LocalModelProfile[], id: string) { return profiles.find((profile) => profile.id === id); }

function localProfileBlurb(id: string): string {
  if (id.endsWith("-mtp")) return "Experimental MTP profile. It reuses the standard target models and projectors, adding small Q8 assistant GGUFs that draft several tokens for llama.cpp to verify in parallel. Requires a recent llama.cpp build.";
  if (id === "8gb-gpu-gemma") return "Uses Gemma 4 E2B Q5 with its F16 audio projector for transcription and E2B Q6 for cleanup. The smaller model leaves roughly 2 GB or more for runtime context.";
  if (id === "12gb-gpu-gemma") return "Uses Gemma 4 E4B Q6 with its F16 audio projector for transcription and Gemma 4 12B Q5 for cleanup. Since the models run sequentially, each stage retains roughly 3-4 GB for runtime context.";
  return "Uses Gemma 4 E4B Q6 with its F16 audio projector for transcription and Gemma 4 12B Q6 for cleanup. Models run sequentially and target a 16 GB GPU.";
}

function runtimeSourceLabel(status: HuggingFaceDownloaderStatus | null): string {
  if (!status?.pythonReady) return "Required";
  if (status.pythonSource === "selected") return "Selected runtime";
  if (status.pythonSource === "managed") return "Managed runtime";
  return "Python on PATH";
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
  const selected = backends.find((backend) => backend.id === selectedBackend);
  const matchedAsset = release?.assets.find((asset) => asset.backend === selectedBackend);
  const selectedIsCurrent = Boolean(status?.installed && currentState?.managed && status.serverPath === currentState.serverPath);
  return (
    <div className="managed-server">
      <div className="managed-server-title">
        <strong>Managed server install</strong>
        <span className={status?.installed ? "env-ok" : "env-missing"}>{status?.installed ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}{status?.installed ? "Installed" : "Not installed"}</span>
      </div>
      <label>
        <TooltipLabel text="Choose the llama.cpp Windows build family to download. Vulkan is the AMD-friendly default; CUDA 12.4 is for NVIDIA.">Backend</TooltipLabel>
        <select value={selectedBackend} onChange={(event) => onBackend(event.target.value as LlamaBackendId)}>{backends.map((backend) => <option key={backend.id} value={backend.id}>{backend.label}</option>)}</select>
      </label>
      <div className="managed-server-note">{selected?.description ?? ""}</div>
      <div className="server-facts">
        <span><strong>Current</strong><small>{currentServerLabel(currentState, currentServerValid)}</small></span>
        <span><strong>Current version</strong><small>{currentState?.version ? currentState.version.split(/\r?\n/)[0] : "Unknown"}</small></span>
        <span><strong>Latest release</strong><small>{release?.releaseTag ?? "Not checked"}</small></span>
        <span><strong>Asset</strong><small>{matchedAsset?.assetName ?? "Not checked"}</small></span>
        <span><strong>Installed</strong><small>{status?.installed ? status.serverPath : "Not installed"}</small></span>
        {status?.version && <span><strong>Version</strong><small>{status.version.split(/\r?\n/)[0]}</small></span>}
        {currentState?.previous && <span><strong>Previous</strong><small>{currentState.previous.releaseTag}</small></span>}
      </div>
      <div className={currentServerValid ? "server-advice info" : "server-advice warn"}>{serverAdvice(currentState, currentServerValid, selectedIsCurrent)}</div>
      <div className="button-row">
        <button type="button" onClick={onCheck} disabled={deleting}>Check latest</button>
        <button type="button" onClick={onDownload} disabled={downloading || deleting}>{downloading ? <LoadingDots /> : <Download size={16} />}{downloading ? "Downloading server..." : "Download server"}</button>
        <button type="button" onClick={onDelete} disabled={downloading || deleting || !status?.installed}>{deleting ? <LoadingDots /> : <Trash2 size={16} />}{deleting ? "Deleting..." : "Delete managed server"}</button>
        <button type="button" className={!currentServerValid && status?.installed ? "primary-inline" : ""} disabled={deleting || !status?.installed || selectedIsCurrent} onClick={() => status?.serverPath && onUse(status.serverPath)}>{selectedIsCurrent ? "Managed server active" : "Use managed server"}</button>
        <button type="button" disabled={deleting || !currentState?.previous?.installed} onClick={() => currentState?.previous?.serverPath && onRevert(currentState.previous.serverPath)}>Revert server</button>
      </div>
    </div>
  );
}

function serverSummary(state: CurrentLlamaServerState | null, pathExists: boolean | undefined): string {
  if (!pathExists) return "Choose or install llama-server";
  if (state?.managed) return `Managed ${state.backend} ${state.releaseTag || ""}`.trim();
  return "Manual llama-server is ready";
}

function currentServerLabel(state: CurrentLlamaServerState | null, valid: boolean): string {
  if (!valid) return "Not ready";
  if (state?.managed) return `Managed ${state.backend} ${state.releaseTag || ""}`.trim();
  return "Manual server";
}

function serverAdvice(state: CurrentLlamaServerState | null, valid: boolean, selectedIsCurrent: boolean): string {
  if (!valid) return "No valid llama-server selected.";
  if (state?.managed) return selectedIsCurrent ? "The current llama-server is managed by the application." : "The current llama-server is managed. You can switch to another downloaded managed build if needed.";
  return "Current server path is manual and valid. Use managed server only if you want to switch.";
}

function PathInput({ label, tip, value, status, onChange, onPick }: { label: string; tip: string; value: string; status?: PathStatus; onChange(value: string): void; onPick(): void }) {
  return (
    <label>
      <span className="path-label"><TooltipLabel text={tip}>{label}</TooltipLabel><span className={status?.exists ? "mini-ok" : "mini-warn"} title={status?.exists ? "File found" : "File missing"}>{status?.exists ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}</span></span>
      <div className="row"><input value={value} onChange={(event) => onChange(event.target.value)} /><button className="icon-button" aria-label={`Choose ${label}`} onClick={onPick} title={`Choose ${label}`}><FolderOpen size={17} /></button></div>
    </label>
  );
}
