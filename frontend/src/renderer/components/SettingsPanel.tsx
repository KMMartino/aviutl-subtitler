import { Settings } from "lucide-react";
import type { CoreWorkflowSettings, CurrentLlamaServerState, EnvStatus, HostedModelVerification, HuggingFaceDownloaderStatus, LlamaBackendId, LlamaBackendOption, LlamaReleaseCheck, LocalModelProfile, LocalModelStatus, ManagedLlamaStatus, PathStatus, RuntimeSetupStatus, WorkflowName } from "../lib/types";
import type { SettingsExpansion } from "../lib/settingsExpansion";
import { isHostedWorkflow, isLocalWorkflow } from "../lib/workflowLabels";
import OutputSettingsSection from "./settings/OutputSettingsSection";
import HostedSettingsSection from "./settings/HostedSettingsSection";
import LocalSettingsSection from "./settings/LocalSettingsSection";
import RuntimeSettingsSection from "./settings/RuntimeSettingsSection";

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
  pythonPath: string;
  pythonReady: boolean;
  runtimeStatus: RuntimeSetupStatus | null;
  runtimeAction: string;
  runtimeFeedback: { section: "python" | "ffmpeg" | "alignment"; text: string; ok: boolean } | null;
  sidecarsEnabled: boolean;
  sidecarDir: string;
  outputPath: string;
  runActive?: boolean;
  expansion: SettingsExpansion;
  onToggleExpansion(section: keyof SettingsExpansion): void;
  onChange(settings: CoreWorkflowSettings): void;
  onPythonPath(path: string): void;
  onEnvFile(path: string): void;
  onSidecar(path: string): void;
  onSidecarsEnabled(value: boolean): void;
  onVerifyHosted(): void;
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
  onRefreshRuntime(action?: string, feedbackSection?: "python" | "ffmpeg" | "alignment"): void;
  onCreateManagedPython(): void;
  onInstallPythonRequirements(): void;
  onDeleteManagedPython(): void;
  onDownloadFfmpeg(): void;
  onDeleteFfmpeg(): void;
  onDownloadAlignment(): void;
  onDeleteAlignment(): void;
};

export default function SettingsPanel({ workflow, settings, envFile, envStatus, hostedVerification, verifyingHosted, pathStatus, modelsDirectory, localModelStatus, localProfiles, localProfileStatuses, selectedLocalProfile, downloadingModels, deletingManaged, modelDownloadMode, hfDownloaderStatus, installingHfDownloader, llamaBackends, selectedLlamaBackend, llamaRelease, managedLlamaStatus, currentLlamaState, downloadingLlama, pythonPath, pythonReady, runtimeStatus, runtimeAction, runtimeFeedback, sidecarsEnabled, sidecarDir, outputPath, runActive = false, expansion, onToggleExpansion, onChange, onPythonPath, onEnvFile, onSidecar, onSidecarsEnabled, onVerifyHosted, onModelsDirectory, onDownloadLocalModels, onDeleteLocalModels, onModelDownloadMode, onInstallHfDownloader, onLocalProfile, onLlamaBackend, onCheckLlamaRelease, onDownloadLlama, onDeleteLlama, onUseManagedLlama, onRevertManagedLlama, onRefreshRuntime, onCreateManagedPython, onInstallPythonRequirements, onDeleteManagedPython, onDownloadFfmpeg, onDeleteFfmpeg, onDownloadAlignment, onDeleteAlignment }: Props) {
  return (
    <section className="panel">
      <div className="panel-title">
        <span><Settings size={18} /> Settings</span>
      </div>
      <fieldset className="settings-lock" disabled={runActive}>
        {isLocalWorkflow(workflow) && (
          <LocalSettingsSection
            settings={settings}
            pathStatus={pathStatus}
            modelsDirectory={modelsDirectory}
            localModelStatus={localModelStatus}
            localProfiles={localProfiles}
            localProfileStatuses={localProfileStatuses}
            selectedLocalProfile={selectedLocalProfile}
            downloadingModels={downloadingModels}
            deletingManaged={deletingManaged}
            modelDownloadMode={modelDownloadMode}
            hfDownloaderStatus={hfDownloaderStatus}
            installingHfDownloader={installingHfDownloader}
            llamaBackends={llamaBackends}
            selectedLlamaBackend={selectedLlamaBackend}
            llamaRelease={llamaRelease}
            managedLlamaStatus={managedLlamaStatus}
            currentLlamaState={currentLlamaState}
            downloadingLlama={downloadingLlama}
            expansion={expansion}
            onToggleExpansion={onToggleExpansion}
            onChange={onChange}
            onModelsDirectory={onModelsDirectory}
            onDownloadLocalModels={onDownloadLocalModels}
            onDeleteLocalModels={onDeleteLocalModels}
            onModelDownloadMode={onModelDownloadMode}
            onInstallHfDownloader={onInstallHfDownloader}
            onLocalProfile={onLocalProfile}
            onLlamaBackend={onLlamaBackend}
            onCheckLlamaRelease={onCheckLlamaRelease}
            onDownloadLlama={onDownloadLlama}
            onDeleteLlama={onDeleteLlama}
            onUseManagedLlama={onUseManagedLlama}
            onRevertManagedLlama={onRevertManagedLlama}
          />
        )}
        <RuntimeSettingsSection
          settings={settings}
          pythonPath={pythonPath}
          pythonReady={pythonReady}
          runtimeStatus={runtimeStatus}
          runtimeAction={runtimeAction}
          runtimeFeedback={runtimeFeedback}
          expansion={expansion}
          onToggle={onToggleExpansion}
          onPythonPath={onPythonPath}
          onRefresh={onRefreshRuntime}
          onCreatePython={onCreateManagedPython}
          onInstallPythonRequirements={onInstallPythonRequirements}
          onDeletePython={onDeleteManagedPython}
          onDownloadFfmpeg={onDownloadFfmpeg}
          onDeleteFfmpeg={onDeleteFfmpeg}
          onDownloadAlignment={onDownloadAlignment}
          onDeleteAlignment={onDeleteAlignment}
        />
        {isHostedWorkflow(workflow) && (
          <HostedSettingsSection settings={settings} envFile={envFile} envStatus={envStatus} verification={hostedVerification} verifying={verifyingHosted} expanded={expansion.env} onToggle={() => onToggleExpansion("env")} onChange={onChange} onEnvFile={onEnvFile} onVerify={onVerifyHosted} />
        )}
        <OutputSettingsSection settings={settings} enabled={sidecarsEnabled} directory={sidecarDir} outputPath={outputPath} onChange={onChange} onDirectory={onSidecar} onEnabled={onSidecarsEnabled} />
      </fieldset>
    </section>
  );
}
