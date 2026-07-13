import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { applySharedAlignment } from "../lib/configPatch";
import type { AppSettings, CoreWorkflowSettings, RuntimeSetupStatus, WorkflowConfig, WorkflowName } from "../lib/types";

export type RuntimeFeedback = {
  section: "python" | "ffmpeg" | "alignment";
  text: string;
  ok: boolean;
};

type Options = {
  appendLog(text: string): void;
  setNotice(text: string): void;
  setSettings: Dispatch<SetStateAction<AppSettings | null>>;
  setConfigs: Dispatch<SetStateAction<Record<WorkflowName, WorkflowConfig> | null>>;
  setCoreSettings: Dispatch<SetStateAction<CoreWorkflowSettings | null>>;
  refreshHfDownloaderStatus(): Promise<void>;
};

export function useRuntimeSetup({ appendLog, setNotice, setSettings, setConfigs, setCoreSettings, refreshHfDownloaderStatus }: Options) {
  const runtimeRequest = useRef(0);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeSetupStatus | null>(null);
  const [runtimeAction, setRuntimeAction] = useState("");
  const [runtimeFeedback, setRuntimeFeedback] = useState<RuntimeFeedback | null>(null);
  const [pythonReady, setPythonReady] = useState(false);

  async function refreshRuntimeStatus(action = "", feedbackSection?: RuntimeFeedback["section"]) {
    const request = ++runtimeRequest.current;
    if (action) {
      setRuntimeAction(action);
      setRuntimeFeedback(null);
    }
    try {
      const status = await window.subtitler.getRuntimeSetupStatus();
      if (request !== runtimeRequest.current) return null;
      setRuntimeStatus(status);
      setPythonReady(status.python.ready && status.python.requirementsInstalled);
      void refreshHfDownloaderStatus();
      if (feedbackSection) {
        const text = feedbackSection === "python" ? "Python runtime status refreshed" : feedbackSection === "ffmpeg" ? "FFmpeg status refreshed" : "Alignment model status refreshed";
        setRuntimeFeedback({ section: feedbackSection, text, ok: true });
        setNotice(text);
      }
      return status;
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      if (feedbackSection) setRuntimeFeedback({ section: feedbackSection, text, ok: false });
      setNotice(text);
      return null;
    } finally {
      if (action) setRuntimeAction("");
    }
  }

  async function createManagedPythonEnv() {
    setRuntimeAction("create-python");
    setRuntimeFeedback(null);
    appendLog(`\n$ managed Python venv setup\n`);
    try {
      await window.subtitler.createManagedPythonEnv();
      await refreshRuntimeStatus();
      await refreshHfDownloaderStatus();
      setRuntimeFeedback({ section: "python", text: "Managed Python venv created", ok: true });
      setNotice("Managed Python venv created");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "python", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function deleteManagedPythonEnv() {
    if (!runtimeStatus?.python.managedInstalled) return;
    if (!window.confirm("Delete the app-managed Python venv from disk? User-selected Python installs will not be touched.")) return;
    setRuntimeAction("delete-python");
    setRuntimeFeedback(null);
    try {
      await window.subtitler.deleteManagedPythonEnv();
      await refreshRuntimeStatus();
      await refreshHfDownloaderStatus();
      setRuntimeFeedback({ section: "python", text: "Managed Python venv deleted", ok: true });
      setNotice("Managed Python venv deleted");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "python", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function installPythonRequirements() {
    setRuntimeAction("install-python");
    setRuntimeFeedback(null);
    appendLog(`\n$ Python requirements install\n`);
    try {
      await window.subtitler.installPythonRequirements();
      await refreshRuntimeStatus();
      await refreshHfDownloaderStatus();
      setRuntimeFeedback({ section: "python", text: "Python requirements installed", ok: true });
      setNotice("Python requirements installed");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "python", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function downloadFfmpeg() {
    setRuntimeAction("download-ffmpeg");
    setRuntimeFeedback(null);
    appendLog(`\n$ FFmpeg download\n`);
    try {
      await window.subtitler.downloadManagedFfmpeg();
      await refreshRuntimeStatus();
      setRuntimeFeedback({ section: "ffmpeg", text: "FFmpeg downloaded", ok: true });
      setNotice("FFmpeg downloaded");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "ffmpeg", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function deleteManagedFfmpeg() {
    if (!runtimeStatus?.ffmpeg.managedInstalled) return;
    if (!window.confirm("Delete app-managed FFmpeg from disk? FFmpeg on PATH will not be touched.")) return;
    setRuntimeAction("delete-ffmpeg");
    setRuntimeFeedback(null);
    try {
      await window.subtitler.deleteManagedFfmpeg();
      await refreshRuntimeStatus();
      setRuntimeFeedback({ section: "ffmpeg", text: "Managed FFmpeg deleted", ok: true });
      setNotice("Managed FFmpeg deleted");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "ffmpeg", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  function synchronizeSharedAlignment(model: string, offlineModelCache: boolean) {
    setSettings((current) => current ? { ...current, alignmentModel: model, alignmentOfflineModelCache: offlineModelCache } : current);
    setConfigs((current) => current ? Object.fromEntries(
      Object.entries(current).map(([name, config]) => [name, applySharedAlignment(config, model, offlineModelCache)])
    ) as Record<WorkflowName, WorkflowConfig> : current);
  }

  async function downloadAlignmentModel() {
    setRuntimeAction("download-alignment");
    setRuntimeFeedback(null);
    appendLog(`\n$ alignment model download\n`);
    try {
      const status = await window.subtitler.downloadAlignmentModel();
      setRuntimeStatus((current) => current ? { ...current, alignment: status } : current);
      synchronizeSharedAlignment(status.modelPath, true);
      setCoreSettings((current) => current ? { ...current, alignment: { model: status.modelPath, offlineModelCache: true } } : current);
      setRuntimeFeedback({ section: "alignment", text: "Alignment model downloaded and verified", ok: true });
      setNotice("Alignment model downloaded and selected");
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "alignment", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function deleteManagedAlignmentModel() {
    if (!runtimeStatus?.alignment.installed || !window.confirm("Delete the app-managed alignment model?")) return;
    setRuntimeAction("delete-alignment");
    try {
      const status = await window.subtitler.deleteAlignmentModel();
      setRuntimeStatus((current) => current ? { ...current, alignment: status } : current);
      synchronizeSharedAlignment("MahmoudAshraf/mms-300m-1130-forced-aligner", false);
      setCoreSettings((current) => current ? { ...current, alignment: { model: "MahmoudAshraf/mms-300m-1130-forced-aligner", offlineModelCache: false } } : current);
      setRuntimeFeedback({ section: "alignment", text: "Managed alignment model deleted", ok: true });
    } catch (error) {
      setRuntimeFeedback({ section: "alignment", text: error instanceof Error ? error.message : String(error), ok: false });
    } finally {
      setRuntimeAction("");
    }
  }

  return {
    runtimeStatus,
    runtimeAction,
    runtimeFeedback,
    pythonReady,
    setPythonReady,
    refreshRuntimeStatus,
    createManagedPythonEnv,
    deleteManagedPythonEnv,
    installPythonRequirements,
    downloadFfmpeg,
    deleteManagedFfmpeg,
    downloadAlignmentModel,
    deleteManagedAlignmentModel,
  };
}
