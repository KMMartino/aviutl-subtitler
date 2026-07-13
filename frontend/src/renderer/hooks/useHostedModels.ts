import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { isHostedSelectionConfigured, isHostedSelectionVerified, selectVerifiedHostedSettings } from "../lib/hostedSelection";
import type { AppSettings, CoreWorkflowSettings, EnvStatus, HostedModelVerification } from "../lib/types";

const emptyEnv: EnvStatus = { exists: false, keysPresent: { OPENAI_API_KEY: false, GEMINI_API_KEY: false } };

type Options = {
  settings: AppSettings | null;
  coreSettings: CoreWorkflowSettings | null;
  setCoreSettings: Dispatch<SetStateAction<CoreWorkflowSettings | null>>;
  setNotice(text: string): void;
};

export function useHostedModels({ settings, coreSettings, setCoreSettings, setNotice }: Options) {
  const requestRevision = useRef(0);
  const [envStatus, setEnvStatus] = useState<EnvStatus>(emptyEnv);
  const [hostedVerification, setHostedVerification] = useState<HostedModelVerification | null>(null);
  const [verifyingHosted, setVerifyingHosted] = useState(false);

  useEffect(() => {
    if (!settings) return;
    const request = ++requestRevision.current;
    setHostedVerification(null);
    setVerifyingHosted(false);
    void window.subtitler.getEnvStatus(settings.envFile).then((status) => {
      if (request === requestRevision.current) setEnvStatus(status);
    });
  }, [settings?.envFile]);

  async function verifyHosted() {
    if (!settings || !coreSettings?.hosted) return;
    const request = ++requestRevision.current;
    setVerifyingHosted(true);
    try {
      const result = await window.subtitler.verifyHostedModels(settings.envFile);
      if (request !== requestRevision.current) return;
      setHostedVerification(result);
      const selected = selectVerifiedHostedSettings(coreSettings, result);
      setCoreSettings(selected.settings);
      setNotice(selected.transcriptionAvailable && selected.cleanupAvailable ? "Hosted models verified" : "Model verification completed with unavailable models");
    } finally {
      if (request === requestRevision.current) setVerifyingHosted(false);
    }
  }

  return {
    envStatus,
    hostedVerification,
    verifyingHosted,
    hostedSelectionReady: isHostedSelectionVerified(coreSettings, hostedVerification) || isHostedSelectionConfigured(coreSettings, envStatus),
    verifyHosted,
  };
}
