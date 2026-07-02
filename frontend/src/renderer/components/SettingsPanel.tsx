import { AlertTriangle, Brain, CheckCircle, ChevronDown, CircleGauge, Download, FolderOpen, Info, RefreshCw, Settings, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type React from "react";
import type { CoreWorkflowSettings, CurrentLlamaServerState, EnvStatus, HostedModelVerification, LlamaBackendId, LlamaBackendOption, LlamaReleaseCheck, LocalModelProfile, LocalModelStatus, ManagedLlamaStatus, PathStatus, WorkflowName } from "../lib/types";
import { isHostedWorkflow, isLocalWorkflow } from "../lib/workflowLabels";
import TooltipLabel from "./TooltipLabel";
import { hostedOptions as catalogHostedOptions, isHostedModelVerified, type HostedOption } from "../../shared/hostedModelCatalog";

type Props = {
  workflow: WorkflowName;
  settings: CoreWorkflowSettings;
  envFile: string;
  envStatus: EnvStatus;
  hostedVerification: HostedModelVerification | null;
  verifyingHosted: boolean;
  pathStatus: Record<string, PathStatus>;
  modelsDirectory: string;
  localModelStatus: LocalModelStatus | null;
  localProfiles: LocalModelProfile[];
  localProfileStatuses: Record<string, LocalModelStatus>;
  selectedLocalProfile: string;
  downloadingModels: boolean;
  llamaBackends: LlamaBackendOption[];
  selectedLlamaBackend: LlamaBackendId;
  llamaRelease: LlamaReleaseCheck | null;
  managedLlamaStatus: ManagedLlamaStatus | null;
  currentLlamaState: CurrentLlamaServerState | null;
  downloadingLlama: boolean;
  pythonPath: string;
  pythonReady: boolean;
  sidecarsEnabled: boolean;
  sidecarDir: string;
  onChange(settings: CoreWorkflowSettings): void;
  onPythonPath(path: string): void;
  onEnvFile(path: string): void;
  onSidecar(path: string): void;
  onSidecarsEnabled(value: boolean): void;
  onVerifyHosted(): void;
  onModelsDirectory(path: string): void;
  onDownloadLocalModels(): void;
  onLocalProfile(profile: string): void;
  onLlamaBackend(value: LlamaBackendId): void;
  onCheckLlamaRelease(): void;
  onDownloadLlama(): void;
  onUseManagedLlama(path: string): void;
  onRevertManagedLlama(path: string): void;
};

export default function SettingsPanel({ workflow, settings, envFile, envStatus, hostedVerification, verifyingHosted, pathStatus, modelsDirectory, localModelStatus, localProfiles, localProfileStatuses, selectedLocalProfile, downloadingModels, llamaBackends, selectedLlamaBackend, llamaRelease, managedLlamaStatus, currentLlamaState, downloadingLlama, pythonPath, pythonReady, sidecarsEnabled, sidecarDir, onChange, onPythonPath, onEnvFile, onSidecar, onSidecarsEnabled, onVerifyHosted, onModelsDirectory, onDownloadLocalModels, onLocalProfile, onLlamaBackend, onCheckLlamaRelease, onDownloadLlama, onUseManagedLlama, onRevertManagedLlama }: Props) {
  const local = settings.local ?? { model: "", mmproj: "", llamaServer: "", cleanupModel: "", cleanupLlamaServer: "", transcriptionDraftModel: "", cleanupDraftModel: "" };
  const hosted = settings.hosted ?? {
    transcriptionProvider: "gemini",
    transcriptionModel: "gemini-3.5-flash",
    fallbackTranscriptionProvider: "openai",
    fallbackTranscriptionModel: "gpt-4o-mini-transcribe",
    cleanupProvider: "openai",
    cleanupModel: "gpt-5.4-mini",
    envFile: ""
  };
  const anyLocalProfileInstalled = Object.values(localProfileStatuses).some((status) => status.installed);
  const selectedLocalProfileInstalled = Boolean(localModelStatus?.installed);
  const [localModelExpanded, setLocalModelExpanded] = useState(true);
  const [pythonExpanded, setPythonExpanded] = useState(!pythonReady);
  const [serverExpanded, setServerExpanded] = useState(!pathStatus.llamaServer?.exists);
  useEffect(() => setLocalModelExpanded(!anyLocalProfileInstalled), [anyLocalProfileInstalled]);
  useEffect(() => setPythonExpanded(!pythonReady), [pythonReady]);
  useEffect(() => setServerExpanded(!pathStatus.llamaServer?.exists), [pathStatus.llamaServer?.exists]);
  function setCost(key: keyof NonNullable<CoreWorkflowSettings["cost"]>, value: number | boolean) {
    onChange({ ...settings, cost: { maxEstimatedApiCostUsd: 5, allowApiSpend: false, estimateCostOnly: false, ...settings.cost, [key]: value } });
  }
  async function pickPath(callback: (path: string) => void, executable = false) {
    const path = executable ? await window.subtitler.chooseExecutable() : await window.subtitler.chooseFile();
    if (path) callback(path);
  }
  async function pickEnv() {
    const path = await window.subtitler.chooseFile();
    if (path) onEnvFile(path);
  }
  async function pickSidecar() {
    const path = await window.subtitler.chooseDirectory();
    if (path) onSidecar(path);
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <span><Settings size={18} /> Settings</span>
      </div>
      {isLocalWorkflow(workflow) && (
        <div className="local-setup">
          {!anyLocalProfileInstalled && (
            <div className="local-blocking-alert">
              <AlertTriangle size={22} />
              <span><strong>No local models installed</strong><small>Local generation is unavailable. Choose a GPU profile and download its models to continue.</small></span>
            </div>
          )}
          <SetupSection
            title="Local model"
            detail={`${selectedProfile(localProfiles, selectedLocalProfile)?.label ?? "Loading profile"} · ${selectedProfile(localProfiles, selectedLocalProfile)?.summary ?? ""}`}
            ready={selectedLocalProfileInstalled}
            expanded={localModelExpanded}
            onToggle={() => setLocalModelExpanded((value) => !value)}
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
            <button onClick={onDownloadLocalModels} disabled={downloadingModels || localModelStatus?.installed}>
              {downloadingModels ? <LoadingDots /> : <Download size={16} />}
              {downloadingModels ? "Downloading models..." : localModelStatus?.installed ? "Models installed" : "Download model profile"}
            </button>
          </div>
          <label>
            <TooltipLabel text="Directory managed by the application for downloaded GGUF models and multimodal projectors.">Models directory</TooltipLabel>
            <div className="row">
              <input value={modelsDirectory} onChange={(event) => onModelsDirectory(event.target.value)} />
              <button className="icon-button" onClick={async () => {
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
            expanded={serverExpanded}
            onToggle={() => setServerExpanded((value) => !value)}
          >
            <PathInput label="llama-server" tip="The llama.cpp server executable for your computer and GPU backend. The same executable is used for transcription and cleanup." value={local.llamaServer} status={pathStatus.llamaServer} onChange={(value) => {
              onChange({ ...settings, local: { ...local, llamaServer: value, cleanupLlamaServer: value } });
            }} onPick={() => pickPath((value) => onChange({ ...settings, local: { ...local, llamaServer: value, cleanupLlamaServer: value } }), true)} />
            <ManagedServerInstall
              backends={llamaBackends}
              selectedBackend={selectedLlamaBackend}
              release={llamaRelease}
              status={managedLlamaStatus}
              currentState={currentLlamaState}
              downloading={downloadingLlama}
              currentServerValid={Boolean(pathStatus.llamaServer?.exists)}
              onBackend={onLlamaBackend}
              onCheck={onCheckLlamaRelease}
              onDownload={onDownloadLlama}
              onUse={onUseManagedLlama}
              onRevert={onRevertManagedLlama}
            />
          </SetupSection>
        </div>
      )}
      <SetupSection
        title="Python runtime"
        detail={pythonReady ? "Runtime responds to --version" : "Choose a Python runtime"}
        ready={pythonReady}
        expanded={pythonExpanded}
        onToggle={() => setPythonExpanded((value) => !value)}
      >
        <PathInput
          label="Python executable"
          tip="Python executable used to run the subtitle engine. The app prefers .venv-win/Scripts/python.exe; change this only when your environment is elsewhere."
          value={pythonPath}
          status={{ path: pythonPath, exists: pythonReady }}
          onChange={onPythonPath}
          onPick={() => pickPath(onPythonPath, true)}
          readyText="Runtime ready"
          missingText="Runtime not ready"
        />
      </SetupSection>
      {isHostedWorkflow(workflow) && (
        <div className="stack">
          <label>
            <TooltipLabel text="Environment file containing GEMINI_API_KEY and OPENAI_API_KEY. Secret values remain in the main process and are never displayed.">Env file</TooltipLabel>
            <div className="row">
              <input value={envFile} onChange={(event) => onEnvFile(event.target.value)} />
              <button className="icon-button" onClick={pickEnv} title="Choose env file"><FolderOpen size={17} /></button>
            </div>
          </label>
          <div className="status-grid">
            <EnvBadge label=".env" ok={envStatus.exists} />
            <EnvBadge label="OPENAI_API_KEY" ok={envStatus.keysPresent.OPENAI_API_KEY} />
            <EnvBadge label="GEMINI_API_KEY" ok={envStatus.keysPresent.GEMINI_API_KEY} />
          </div>
          <button onClick={onVerifyHosted} disabled={verifyingHosted || !envStatus.exists}>
            <RefreshCw size={16} className={verifyingHosted ? "spin" : ""} /> {verifyingHosted ? "Checking APIs" : "Verify API models"}
          </button>
          {hostedVerification && <VerificationSummary result={hostedVerification} />}
          <HostedModelSelect
            label="Transcription model"
            tip="Only verified supported transcription models are offered: OpenAI GPT-4o Transcribe or Gemini 3.5 Flash."
            options={hostedOptions(hostedVerification, "transcription")}
            value={`${hosted.transcriptionProvider}:${hosted.transcriptionModel}`}
            onChange={(provider, model) => onChange({ ...settings, hosted: { ...hosted, transcriptionProvider: provider, transcriptionModel: model } })}
          />
          <HostedModelSelect
            label="Fallback model"
            tip="Used once when the transcription model returns a malformed response. This has mostly been seen with Gemini responses that are empty, malformed, or implausibly long."
            options={hostedOptions(hostedVerification, "transcription")}
            value={`${hosted.fallbackTranscriptionProvider}:${hosted.fallbackTranscriptionModel}`}
            onChange={(provider, model) => onChange({ ...settings, hosted: { ...hosted, fallbackTranscriptionProvider: provider, fallbackTranscriptionModel: model } })}
          />
          <HostedModelSelect
            label="Cleanup model"
            tip="Only verified supported cleanup models are offered: OpenAI GPT-5.4 mini or Gemini 3.5 Flash."
            options={hostedOptions(hostedVerification, "cleanup")}
            value={`${hosted.cleanupProvider}:${hosted.cleanupModel}`}
            onChange={(provider, model) => onChange({ ...settings, hosted: { ...hosted, cleanupProvider: provider, cleanupModel: model } })}
          />
          <div className="two-col">
            <label><TooltipLabel text="Run is blocked before hosted requests when the estimated total exceeds this amount and Allow spend is off.">Max API estimate USD</TooltipLabel><input type="number" min={0} step={0.1} value={settings.cost?.maxEstimatedApiCostUsd ?? 5} onChange={(event) => setCost("maxEstimatedApiCostUsd", Number(event.target.value))} /></label>
            <label className="check"><input type="checkbox" checked={settings.cost?.allowApiSpend ?? false} onChange={(event) => setCost("allowApiSpend", event.target.checked)} /><TooltipLabel text="Permit a hosted run even when its estimate exceeds the configured maximum. This does not itself start a run.">Allow spend</TooltipLabel></label>
          </div>
          <label className="check"><input type="checkbox" checked={settings.cost?.estimateCostOnly ?? false} onChange={(event) => setCost("estimateCostOnly", event.target.checked)} /><TooltipLabel text="Perform audio preparation and speech selection, report the estimate, then stop before transcription and subtitle generation.">Estimate cost only</TooltipLabel></label>
        </div>
      )}
      <div className="sidecar-settings">
        <span className="field-label-line">
          <TooltipLabel text="Sidecar files are auxiliary run outputs such as run JSON, final cleaned text, review notes, and optional diagnostics.">Sidecar files</TooltipLabel>
          <label className="switch-label">
            <input className="switch" type="checkbox" checked={sidecarsEnabled} onChange={(event) => onSidecarsEnabled(event.target.checked)} />
            {sidecarsEnabled ? "On" : "Off"}
          </label>
        </span>
        {sidecarsEnabled ? (
          <>
            <label>
              <TooltipLabel text="Directory where sidecar files are written and opened from the Outputs panel.">Sidecar directory</TooltipLabel>
              <div className="row">
                <input value={sidecarDir} onChange={(event) => onSidecar(event.target.value)} />
                <button className="icon-button" onClick={pickSidecar} title="Choose sidecar directory"><FolderOpen size={17} /></button>
              </div>
            </label>
            <label className="check"><input type="checkbox" checked={settings.diagnostics.profile} onChange={(event) => onChange({ ...settings, diagnostics: { profile: event.target.checked } })} /><TooltipLabel text="Add timing and profiling diagnostics to the sidecar output set.">Write diagnostics</TooltipLabel></label>
          </>
        ) : (
          <div className="disabled-field">Run JSON, final text, review notes, and diagnostics will not be written.</div>
        )}
      </div>
    </section>
  );
}

function selectedProfile(profiles: LocalModelProfile[], id: string) {
  return profiles.find((profile) => profile.id === id);
}

function localProfileBlurb(id: string): string {
  if (id.endsWith("-mtp")) return "Experimental MTP profile. It reuses the standard target models and projectors, adding small Q8 assistant GGUFs that draft several tokens for llama.cpp to verify in parallel. Requires a recent llama.cpp build.";
  if (id === "8gb-gpu-gemma") return "Uses Gemma 4 E2B Q5 with its F16 audio projector for transcription and E2B Q6 for cleanup. The smaller model leaves roughly 2 GB or more for runtime context.";
  if (id === "12gb-gpu-gemma") return "Uses Gemma 4 E4B Q6 with its F16 audio projector for transcription and Gemma 4 12B Q5 for cleanup. Since the models run sequentially, each stage retains roughly 3-4 GB for runtime context.";
  return "Uses Gemma 4 E4B Q6 with its F16 audio projector for transcription and Gemma 4 12B Q6 for cleanup. Models run sequentially and target a 16 GB GPU.";
}

function LoadingDots() {
  return <span className="loading-dots" aria-hidden="true"><i /><i /><i /></span>;
}

function SetupSection({ title, detail, ready, expanded, onToggle, children }: {
  title: string;
  detail: string;
  ready: boolean;
  expanded: boolean;
  onToggle(): void;
  children: React.ReactNode;
}) {
  return (
    <div className="setup-section">
      <button type="button" className="setup-summary" onClick={onToggle} aria-expanded={expanded}>
        <span>
          <strong>{title}</strong>
          <small>{detail}</small>
        </span>
        <span className={ready ? "setup-ready" : "setup-required"}>
          {ready ? <CheckCircle size={15} /> : <AlertTriangle size={15} />}
          {ready ? "Ready" : "Not ready"}
          <ChevronDown size={16} className={expanded ? "chevron-open" : ""} />
        </span>
      </button>
      <div className={`setup-content-outer ${expanded ? "expanded" : ""}`}>
        <div className="stack setup-content">{children}</div>
      </div>
    </div>
  );
}

function ManagedFile({ label, file }: { label: string; file: { path: string; exists: boolean } }) {
  return <div title={file.path}>{file.exists ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}<span>{label}</span></div>;
}

function ManagedServerInstall({ backends, selectedBackend, release, status, currentState, downloading, currentServerValid, onBackend, onCheck, onDownload, onUse, onRevert }: {
  backends: LlamaBackendOption[];
  selectedBackend: LlamaBackendId;
  release: LlamaReleaseCheck | null;
  status: ManagedLlamaStatus | null;
  currentState: CurrentLlamaServerState | null;
  downloading: boolean;
  currentServerValid: boolean;
  onBackend(value: LlamaBackendId): void;
  onCheck(): void;
  onDownload(): void;
  onUse(path: string): void;
  onRevert(path: string): void;
}) {
  const selected = backends.find((backend) => backend.id === selectedBackend);
  const matchedAsset = release?.assets.find((asset) => asset.backend === selectedBackend);
  const selectedIsCurrent = Boolean(status?.installed && currentState?.managed && status.serverPath === currentState.serverPath);
  return (
    <div className="managed-server">
      <div className="managed-server-title">
        <strong>Managed server install</strong>
        <span className={status?.installed ? "env-ok" : "env-missing"}>
          {status?.installed ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}
          {status?.installed ? "Installed" : "Not installed"}
        </span>
      </div>
      <label>
        <TooltipLabel text="Choose the llama.cpp Windows build family to download. Vulkan is the AMD-friendly default; CUDA 12.4 is for NVIDIA.">Backend</TooltipLabel>
        <select value={selectedBackend} onChange={(event) => onBackend(event.target.value as LlamaBackendId)}>
          {backends.map((backend) => <option key={backend.id} value={backend.id}>{backend.label}</option>)}
        </select>
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
      <div className={currentServerValid ? "server-advice info" : "server-advice warn"}>
        {serverAdvice(currentState, currentServerValid, selectedIsCurrent)}
      </div>
      <div className="button-row">
        <button type="button" onClick={onCheck}>Check latest</button>
        <button type="button" onClick={onDownload} disabled={downloading}>
          {downloading ? <LoadingDots /> : <Download size={16} />}
          {downloading ? "Downloading server..." : "Download server"}
        </button>
        <button type="button" className={!currentServerValid && status?.installed ? "primary-inline" : ""} disabled={!status?.installed || selectedIsCurrent} onClick={() => status?.serverPath && onUse(status.serverPath)}>
          {selectedIsCurrent ? "Managed server active" : "Use managed server"}
        </button>
        <button type="button" disabled={!currentState?.previous?.installed} onClick={() => currentState?.previous?.serverPath && onRevert(currentState.previous.serverPath)}>
          Revert server
        </button>
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
  if (state?.managed) {
    if (selectedIsCurrent) return "The current llama-server is managed by the application.";
    return "The current llama-server is managed. You can switch to another downloaded managed build if needed.";
  }
  return "Current server path is manual and valid. Use managed server only if you want to switch.";
}

function hostedOptions(result: HostedModelVerification | null, role: "transcription" | "cleanup"): HostedOption[] {
  return result ? catalogHostedOptions(role).filter((option) => isHostedModelVerified(option.provider, option.model, role, result)) : [];
}

function HostedModelSelect({ label, tip, options, value, onChange }: { label: string; tip: string; options: HostedOption[]; value: string; onChange(provider: "openai" | "gemini", model: string): void }) {
  const selected = options.find((option) => `${option.provider}:${option.model}` === value) ?? options[0];
  return (
    <label>
      <TooltipLabel text={tip}>{label}</TooltipLabel>
      {options.length > 1 && selected ? (
        <ModelDropdown options={options} selected={selected} onChange={onChange} />
      ) : selected ? (
        <div className="model-option-row disabled-field"><span>{selected.label}</span><ModelTraits option={selected} /></div>
      ) : (
        <div className="disabled-field">Verify API models to select</div>
      )}
    </label>
  );
}

function ModelDropdown({ options, selected, onChange }: { options: HostedOption[]; selected: HostedOption; onChange(provider: "openai" | "gemini", model: string): void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="model-dropdown" ref={root}>
      <button type="button" className="model-dropdown-trigger" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{selected.label}</span><ModelTraits option={selected} />
      </button>
      {open && <div className="model-dropdown-menu" role="listbox">
        {options.map((option) => (
          <button
            type="button"
            role="option"
            aria-selected={option.model === selected.model && option.provider === selected.provider}
            className="model-option-row"
            key={`${option.provider}:${option.model}`}
            onClick={() => {
              onChange(option.provider, option.model);
              setOpen(false);
            }}
          >
            <span>{option.label}</span><ModelTraits option={option} />
          </button>
        ))}
      </div>}
    </div>
  );
}

function ModelTraits({ option }: { option: HostedOption }) {
  const Icon = option.emphasis === "quality" ? Brain : option.emphasis === "speed" ? Zap : CircleGauge;
  const title = option.emphasis === "quality" ? "Quality focused" : option.emphasis === "speed" ? "Speed focused" : "Balanced";
  return (
    <span className={`model-traits traits-${option.emphasis}`} onClick={(event) => event.stopPropagation()}>
      <span title={title}><Icon size={16} /></span>
      <span className="tooltip model-info" tabIndex={0}>
        <Info size={15} />
        <span className="tooltip-content">{option.blurb}</span>
      </span>
    </span>
  );
}

function VerificationSummary({ result }: { result: HostedModelVerification }) {
  return (
    <div className="verification-grid">
      <ProviderVerification label="OpenAI" result={result.openai} />
      <ProviderVerification label="Gemini" result={result.gemini} />
    </div>
  );
}

function ProviderVerification({ label, result }: { label: string; result: HostedModelVerification["openai"] | HostedModelVerification["gemini"] }) {
  const cleanupCount = Number(result.cleanup) + Number("cleanup55" in result && result.cleanup55) + Number("cleanup31Pro" in result && result.cleanup31Pro) + Number("cleanup31FlashLite" in result && result.cleanup31FlashLite);
  const transcriptionCount = Number(result.transcription) + Number("transcriptionMini" in result && result.transcriptionMini) + Number("transcription31Pro" in result && result.transcription31Pro) + Number("transcription31FlashLite" in result && result.transcription31FlashLite);
  const ok = transcriptionCount > 0 || cleanupCount > 0;
  const detail = !result.keyPresent ? "No key" : result.error || `${transcriptionCount} transcription; ${cleanupCount} cleanup model${cleanupCount === 1 ? "" : "s"}`;
  return <div className={ok ? "verification-ok" : "verification-failed"} title={detail}>{ok ? <CheckCircle size={15} /> : <AlertTriangle size={15} />}<span><strong>{label}</strong><small>{detail}</small></span></div>;
}

function PathInput({ label, tip, value, status, onChange, onPick, readyText = "File found", missingText = "File missing" }: { label: string; tip: string; value: string; status?: PathStatus; onChange(value: string): void; onPick(): void; readyText?: string; missingText?: string }) {
  return (
    <label>
      <span className="path-label">
        <TooltipLabel text={tip}>{label}</TooltipLabel>
        <span className={status?.exists ? "mini-ok" : "mini-warn"} title={status?.exists ? readyText : missingText}>
          {status?.exists ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
        </span>
      </span>
      <div className="row">
        <input value={value} onChange={(event) => onChange(event.target.value)} />
        <button className="icon-button" onClick={onPick} title={`Choose ${label}`}><FolderOpen size={17} /></button>
      </div>
    </label>
  );
}

function EnvBadge({ label, ok }: { label: string; ok: boolean }) {
  return <span className={ok ? "env-ok" : "env-missing"}>{ok ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}{label}</span>;
}
