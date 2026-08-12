import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { applySharedAlignment } from "../lib/configPatch";
import type { AppSettings, CoreWorkflowSettings, RuntimeSetupStatus, WorkflowConfig, WorkflowName } from "../lib/types";
import { useI18n } from "../i18n";

export type RuntimeFeedback = {
  section: "python" | "ffmpeg" | "ytDlp" | "alignment";
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
  const { t } = useI18n();
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
        const text = feedbackSection === "python"
          ? t("notice.pythonStatusRefreshed")
          : feedbackSection === "ffmpeg"
            ? t("notice.ffmpegStatusRefreshed")
            : feedbackSection === "ytDlp"
              ? t("notice.ytdlpStatusRefreshed")
              : t("notice.alignmentStatusRefreshed");
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
    appendLog(`\n$ ${t("notice.pythonSetupLog")}\n`);
    try {
      await window.subtitler.createManagedPythonEnv();
      await refreshRuntimeStatus();
      await refreshHfDownloaderStatus();
      setRuntimeFeedback({ section: "python", text: t("notice.pythonCreated"), ok: true });
      setNotice(t("notice.pythonCreated"));
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
    if (!window.confirm(t("notice.deletePythonConfirm"))) return;
    setRuntimeAction("delete-python");
    setRuntimeFeedback(null);
    try {
      await window.subtitler.deleteManagedPythonEnv();
      await refreshRuntimeStatus();
      await refreshHfDownloaderStatus();
      setRuntimeFeedback({ section: "python", text: t("notice.pythonDeleted"), ok: true });
      setNotice(t("notice.pythonDeleted"));
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
    appendLog(`\n$ ${t("notice.pythonRequirementsLog")}\n`);
    try {
      await window.subtitler.installPythonRequirements();
      await refreshRuntimeStatus();
      await refreshHfDownloaderStatus();
      setRuntimeFeedback({ section: "python", text: t("notice.pythonRequirementsInstalled"), ok: true });
      setNotice(t("notice.pythonRequirementsInstalled"));
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
    appendLog(`\n$ ${t("notice.ffmpegDownloadLog")}\n`);
    try {
      await window.subtitler.downloadManagedFfmpeg();
      await refreshRuntimeStatus();
      setRuntimeFeedback({ section: "ffmpeg", text: t("notice.ffmpegDownloaded"), ok: true });
      setNotice(t("notice.ffmpegDownloaded"));
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
    if (!window.confirm(t("notice.deleteFfmpegConfirm"))) return;
    setRuntimeAction("delete-ffmpeg");
    setRuntimeFeedback(null);
    try {
      await window.subtitler.deleteManagedFfmpeg();
      await refreshRuntimeStatus();
      setRuntimeFeedback({ section: "ffmpeg", text: t("notice.ffmpegDeleted"), ok: true });
      setNotice(t("notice.ffmpegDeleted"));
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "ffmpeg", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function installOrUpdateYtDlp() {
    setRuntimeAction("update-ytdlp");
    setRuntimeFeedback(null);
    appendLog(`\n$ ${t("notice.ytdlpUpdateLog")}\n`);
    try {
      await window.subtitler.installOrUpdateYtDlp();
      await refreshRuntimeStatus();
      setRuntimeFeedback({ section: "ytDlp", text: t("notice.ytdlpCurrent"), ok: true });
      setNotice(t("notice.ytdlpCurrent"));
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "ytDlp", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function deleteManagedYtDlp() {
    if (!runtimeStatus?.ytDlp.managedInstalled) return;
    if (!window.confirm(t("notice.deleteYtdlpConfirm"))) return;
    setRuntimeAction("delete-ytdlp");
    setRuntimeFeedback(null);
    try {
      await window.subtitler.deleteManagedYtDlp();
      await refreshRuntimeStatus();
      setRuntimeFeedback({ section: "ytDlp", text: t("notice.ytdlpDeleted"), ok: true });
      setNotice(t("notice.ytdlpDeleted"));
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "ytDlp", text, ok: false });
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
    appendLog(`\n$ ${t("notice.alignmentDownloadLog")}\n`);
    try {
      const status = await window.subtitler.downloadAlignmentModel();
      setRuntimeStatus((current) => current ? { ...current, alignment: status } : current);
      synchronizeSharedAlignment(status.modelPath, true);
      setCoreSettings((current) => current ? { ...current, alignment: { model: status.modelPath, offlineModelCache: true } } : current);
      setRuntimeFeedback({ section: "alignment", text: t("notice.alignmentDownloadedVerified"), ok: true });
      setNotice(t("notice.alignmentDownloadedSelected"));
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setRuntimeFeedback({ section: "alignment", text, ok: false });
      setNotice(text);
    } finally {
      setRuntimeAction("");
    }
  }

  async function deleteManagedAlignmentModel() {
    if (!runtimeStatus?.alignment.installed || !window.confirm(t("notice.deleteAlignmentConfirm"))) return;
    setRuntimeAction("delete-alignment");
    try {
      const status = await window.subtitler.deleteAlignmentModel();
      setRuntimeStatus((current) => current ? { ...current, alignment: status } : current);
      synchronizeSharedAlignment("MahmoudAshraf/mms-300m-1130-forced-aligner", false);
      setCoreSettings((current) => current ? { ...current, alignment: { model: "MahmoudAshraf/mms-300m-1130-forced-aligner", offlineModelCache: false } } : current);
      setRuntimeFeedback({ section: "alignment", text: t("notice.alignmentDeleted"), ok: true });
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
    installOrUpdateYtDlp,
    deleteManagedYtDlp,
    downloadAlignmentModel,
    deleteManagedAlignmentModel,
  };
}
