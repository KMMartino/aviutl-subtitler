import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { applyCoreSettings } from "../lib/configPatch";
import type { AppSettings, CoreWorkflowSettings, CurrentLlamaServerState, LlamaBackendId, LlamaBackendOption, LlamaReleaseCheck, ManagedLlamaStatus, WorkflowConfig, WorkflowName } from "../lib/types";

type Options = {
  settings: AppSettings | null;
  coreSettings: CoreWorkflowSettings | null;
  configs: Record<WorkflowName, WorkflowConfig> | null;
  workflow: WorkflowName;
  setCoreSettings: Dispatch<SetStateAction<CoreWorkflowSettings | null>>;
  setConfigs: Dispatch<SetStateAction<Record<WorkflowName, WorkflowConfig> | null>>;
  setManagedDeleteAction: Dispatch<SetStateAction<string>>;
  appendLog(text: string): void;
  setNotice(text: string): void;
  refreshPathStatus(core: CoreWorkflowSettings): Promise<void>;
};

export function useManagedLlama({ settings, coreSettings, configs, workflow, setCoreSettings, setConfigs, setManagedDeleteAction, appendLog, setNotice, refreshPathStatus }: Options) {
  const requestRevision = useRef(0);
  const [llamaBackends, setLlamaBackends] = useState<LlamaBackendOption[]>([]);
  const [llamaRelease, setLlamaRelease] = useState<LlamaReleaseCheck | null>(null);
  const [managedLlamaStatus, setManagedLlamaStatus] = useState<ManagedLlamaStatus | null>(null);
  const [currentLlamaState, setCurrentLlamaState] = useState<CurrentLlamaServerState | null>(null);
  const [downloadingLlama, setDownloadingLlama] = useState(false);

  async function refreshManagedLlama(backend: LlamaBackendId, releaseTag?: string) {
    const request = ++requestRevision.current;
    const status = await window.subtitler.getManagedLlamaStatus(backend, releaseTag);
    if (request !== requestRevision.current || settings?.llamaBackend !== backend) return;
    setManagedLlamaStatus(status);
  }

  async function checkLlamaRelease() {
    appendLog(`\n$ llama.cpp server release check\n`);
    try {
      const result = await window.subtitler.checkLatestLlamaRelease();
      setLlamaRelease(result);
      if (settings) {
        const status = await window.subtitler.getManagedLlamaStatus(settings.llamaBackend, undefined);
        setManagedLlamaStatus(status);
      }
      setNotice(`Latest llama.cpp: ${result.releaseTag}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    }
  }

  async function downloadLlama() {
    if (!settings) return;
    setDownloadingLlama(true);
    try {
      const status = await window.subtitler.downloadManagedLlamaServer(settings.llamaBackend);
      setManagedLlamaStatus(status);
      setLlamaRelease((current) => current?.releaseTag === status.releaseTag ? current : {
        releaseTag: status.releaseTag,
        assets: [],
        checkedAt: new Date().toISOString()
      });
      await useManagedLlama(status.serverPath);
      setNotice("llama-server downloaded and selected");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadingLlama(false);
    }
  }

  async function deleteManagedLlama() {
    if (!settings || !managedLlamaStatus?.installed) return;
    if (!window.confirm("Delete this app-managed llama-server backend from disk? Manual server paths will not be touched.")) return;
    setManagedDeleteAction("llama");
    try {
      const status = await window.subtitler.deleteManagedLlamaServer(settings.llamaBackend);
      setManagedLlamaStatus(status);
      if (coreSettings?.local) {
        await refreshPathStatus(coreSettings);
        setCurrentLlamaState(await window.subtitler.getCurrentLlamaServerState(coreSettings.local.llamaServer));
      }
      setNotice("Managed llama-server deleted");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setManagedDeleteAction("");
    }
  }

  async function useManagedLlama(path: string) {
    if (!coreSettings?.local || !settings || !configs) return;
    const nextCore = {
      ...coreSettings,
      local: {
        ...coreSettings.local,
        llamaServer: path,
        cleanupLlamaServer: path
      }
    };
    setCoreSettings(nextCore);
    const workflowConfig = applyCoreSettings(configs[workflow], nextCore, workflow);
    await window.subtitler.saveWorkflowConfig(workflow, workflowConfig);
    setConfigs({ ...configs, [workflow]: workflowConfig });
    setNotice("Managed llama-server selected");
  }

  return {
    llamaBackends,
    setLlamaBackends,
    llamaRelease,
    managedLlamaStatus,
    setManagedLlamaStatus,
    currentLlamaState,
    setCurrentLlamaState,
    downloadingLlama,
    refreshManagedLlama,
    checkLlamaRelease,
    downloadLlama,
    deleteManagedLlama,
    useManagedLlama,
  };
}
