import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { AppSettings, CoreWorkflowSettings, HuggingFaceDownloaderStatus, LocalModelProfile, LocalModelStatus } from "../lib/types";
import { applyLocalProfile, matchesLocalProfile } from "../lib/localProfileSettings";
import { useI18n } from "../i18n";

type Options = {
  settings: AppSettings | null;
  coreSettings: CoreWorkflowSettings | null;
  setCoreSettings: Dispatch<SetStateAction<CoreWorkflowSettings | null>>;
  appendLog(text: string): void;
  setNotice(text: string): void;
  setManagedDeleteAction: Dispatch<SetStateAction<string>>;
};

export function useLocalModels({ settings, coreSettings, setCoreSettings, appendLog, setNotice, setManagedDeleteAction }: Options) {
  const { t } = useI18n();
  const requestRevision = useRef(0);
  const [localModelStatus, setLocalModelStatus] = useState<LocalModelStatus | null>(null);
  const [localProfileStatuses, setLocalProfileStatuses] = useState<Record<string, LocalModelStatus>>({});
  const [localProfiles, setLocalProfiles] = useState<LocalModelProfile[]>([]);
  const [downloadingModels, setDownloadingModels] = useState(false);
  const [hfDownloaderStatus, setHfDownloaderStatus] = useState<HuggingFaceDownloaderStatus | null>(null);
  const [installingHfDownloader, setInstallingHfDownloader] = useState(false);

  useEffect(() => {
    if (!coreSettings?.local || !localModelStatus) return;
    const profile = localProfiles.find((item) => item.id === settings?.localModelProfile);
    if (matchesLocalProfile(coreSettings, localModelStatus, profile)) return;
    setCoreSettings(applyLocalProfile(coreSettings, localModelStatus, profile));
  }, [localModelStatus, coreSettings, localProfiles, settings?.localModelProfile, setCoreSettings]);

  async function refreshLocalModels(modelsDirectory: string, profileId: string) {
    const request = ++requestRevision.current;
    const statuses = await Promise.all(localProfiles.map((profile) => window.subtitler.getLocalModelStatus(modelsDirectory, profile.id)));
    if (request !== requestRevision.current) return;
    const byProfile = Object.fromEntries(statuses.map((status) => [status.profile, status]));
    setLocalProfileStatuses(byProfile);
    setLocalModelStatus(byProfile[profileId] ?? null);
  }

  async function refreshHfDownloaderStatus() {
    try {
      setHfDownloaderStatus(await window.subtitler.getHuggingFaceDownloaderStatus());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    }
  }

  async function installHfDownloader() {
    if (hfDownloaderStatus?.ready) return;
    if (hfDownloaderStatus?.pythonReady && hfDownloaderStatus.pythonSource !== "managed") {
      const target = hfDownloaderStatus.pythonPath || t("notice.activePython");
      if (!window.confirm(t("notice.hfInstallConfirm", { target }))) return;
    }
    setInstallingHfDownloader(true);
    appendLog(`\n$ ${t("notice.hfInstallLog")}\n`);
    try {
      const status = await window.subtitler.installHuggingFaceDownloader();
      setHfDownloaderStatus(status);
      setNotice(t("notice.hfInstalled"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setInstallingHfDownloader(false);
    }
  }

  async function downloadLocalModels() {
    if (!settings) return;
    const verifyingExisting = Boolean(localModelStatus?.needsVerification);
    setDownloadingModels(true);
    appendLog(`\n$ ${verifyingExisting ? t("notice.verifyModelsLog") : t("notice.downloadModelsLog")}\n`);
    try {
      const status = await window.subtitler.downloadLocalProfile(settings.modelsDirectory, settings.localModelProfile, settings.modelDownloadMode ?? "direct");
      setLocalModelStatus(status);
      setLocalProfileStatuses((current) => ({ ...current, [status.profile]: status }));
      if (coreSettings?.local) {
        const profile = localProfiles.find((item) => item.id === settings.localModelProfile);
        const next = applyLocalProfile(coreSettings, status, profile);
        setCoreSettings(next);
      }
      setNotice(verifyingExisting ? t("notice.modelsVerified") : t("notice.profileInstalled"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadingModels(false);
    }
  }

  async function deleteLocalModels() {
    if (!settings || !localModelStatus?.managed) return;
    if (!window.confirm(t("notice.deleteProfileConfirm"))) return;
    setManagedDeleteAction("models");
    try {
      const status = await window.subtitler.deleteManagedLocalProfile(settings.modelsDirectory, settings.localModelProfile);
      setLocalModelStatus(status);
      setLocalProfileStatuses((current) => ({ ...current, [status.profile]: status }));
      await refreshLocalModels(settings.modelsDirectory, settings.localModelProfile);
      setNotice(t("notice.profileDeleted"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setManagedDeleteAction("");
    }
  }

  return {
    localModelStatus,
    setLocalModelStatus,
    localProfileStatuses,
    localProfiles,
    setLocalProfiles,
    downloadingModels,
    hfDownloaderStatus,
    installingHfDownloader,
    refreshLocalModels,
    refreshHfDownloaderStatus,
    installHfDownloader,
    downloadLocalModels,
    deleteLocalModels,
  };
}
