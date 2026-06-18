import { useEffect, useMemo, useState } from "react";
import ModeSelector from "./components/ModeSelector";
import ThemeSelector from "./components/ThemeSelector";
import InputPanel from "./components/InputPanel";
import SettingsPanel from "./components/SettingsPanel";
import GlossaryPanel from "./components/GlossaryPanel";
import RunPanel from "./components/RunPanel";
import LogViewer from "./components/LogViewer";
import OutputPanel from "./components/OutputPanel";
import { applyCoreSettings, extractCoreSettings } from "./lib/configPatch";
import { defaultOutputPath, defaultSidecarDir } from "./lib/paths";
import type { AppSettings, CoreWorkflowSettings, CurrentLlamaServerState, EnvStatus, HostedModelVerification, LlamaBackendId, LlamaBackendOption, LlamaReleaseCheck, LocalModelProfile, LocalModelStatus, ManagedLlamaStatus, MediaAnalysis, PathStatus, RunEvent, RunState, WorkflowConfig, WorkflowName } from "./lib/types";
import { isHostedWorkflow, isLocalWorkflow } from "./lib/workflowLabels";

const emptyEnv: EnvStatus = { exists: false, keysPresent: { OPENAI_API_KEY: false, GEMINI_API_KEY: false } };

export default function App() {
  const [projectRoot, setProjectRoot] = useState("");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [configs, setConfigs] = useState<Record<WorkflowName, WorkflowConfig> | null>(null);
  const [configPaths, setConfigPaths] = useState<Record<WorkflowName, string> | null>(null);
  const [coreSettings, setCoreSettings] = useState<CoreWorkflowSettings | null>(null);
  const [inputPath, setInputPath] = useState("");
  const [outputPath, setOutputPath] = useState("");
  const [sidecarDir, setSidecarDir] = useState("");
  const [envStatus, setEnvStatus] = useState<EnvStatus>(emptyEnv);
  const [hostedVerification, setHostedVerification] = useState<HostedModelVerification | null>(null);
  const [verifyingHosted, setVerifyingHosted] = useState(false);
  const [localModelStatus, setLocalModelStatus] = useState<LocalModelStatus | null>(null);
  const [localProfileStatuses, setLocalProfileStatuses] = useState<Record<string, LocalModelStatus>>({});
  const [localProfiles, setLocalProfiles] = useState<LocalModelProfile[]>([]);
  const [downloadingModels, setDownloadingModels] = useState(false);
  const [llamaBackends, setLlamaBackends] = useState<LlamaBackendOption[]>([]);
  const [llamaRelease, setLlamaRelease] = useState<LlamaReleaseCheck | null>(null);
  const [managedLlamaStatus, setManagedLlamaStatus] = useState<ManagedLlamaStatus | null>(null);
  const [currentLlamaState, setCurrentLlamaState] = useState<CurrentLlamaServerState | null>(null);
  const [downloadingLlama, setDownloadingLlama] = useState(false);
  const [pythonReady, setPythonReady] = useState(false);
  const [pathStatus, setPathStatus] = useState<Record<string, PathStatus>>({});
  const [glossary, setGlossary] = useState("");
  const [logs, setLogs] = useState("");
  const [runState, setRunState] = useState<RunState>("idle");
  const [activeRunId, setActiveRunId] = useState("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [notice, setNotice] = useState("");
  const [analysis, setAnalysis] = useState<MediaAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState("");

  const workflow = settings?.selectedWorkflow ?? "local";
  const hostedReady = !isHostedWorkflow(workflow) || isHostedSelectionVerified(coreSettings, hostedVerification);
  const localReady = !isLocalWorkflow(workflow) || Boolean(localModelStatus?.installed && pathStatus.llamaServer?.exists);
  const canRun = Boolean(settings && configs && configPaths && inputPath && outputPath && pythonReady && hostedReady && localReady);

  useEffect(() => {
    void loadInitialState();
    void window.subtitler.listLocalProfiles().then(setLocalProfiles);
    void window.subtitler.listLlamaBackends().then(setLlamaBackends);
    return window.subtitler.onRunEvent(handleRunEvent);
  }, []);

  useEffect(() => {
    if (!settings || !configs) return;
    setCoreSettings(extractCoreSettings(configs[settings.selectedWorkflow]));
  }, [settings?.selectedWorkflow, configs]);

  useEffect(() => {
    if (settings) document.documentElement.dataset.theme = settings.theme;
  }, [settings?.theme]);

  useEffect(() => {
    if (!inputPath || !settings) return;
    setOutputPath(defaultOutputPath(inputPath, settings.selectedWorkflow));
    setSidecarDir(defaultSidecarDir(inputPath));
  }, [inputPath, settings?.selectedWorkflow]);

  useEffect(() => {
    if (!inputPath) {
      setAnalysis(null);
      setAnalysisError("");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setAnalyzing(true);
      setAnalysisError("");
      try {
        const result = await window.subtitler.analyzeMedia(inputPath);
        if (cancelled) return;
        setAnalysis(result);
        setCoreSettings((current) => {
          if (!current || !result.audioTracks.length || result.audioTracks.some((track) => track.audioIndex === current.audioTrack)) return current;
          return { ...current, audioTrack: result.audioTracks[0].audioIndex };
        });
      } catch (error) {
        if (cancelled) return;
        setAnalysis(null);
        setAnalysisError(error instanceof Error ? error.message : String(error));
      } finally {
        if (!cancelled) setAnalyzing(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [inputPath]);

  useEffect(() => {
    if (!settings) return;
    setHostedVerification(null);
    void window.subtitler.getEnvStatus(settings.envFile).then(setEnvStatus);
  }, [settings?.envFile]);

  useEffect(() => {
    if (!settings) return;
    let cancelled = false;
    void window.subtitler.pythonReady(settings.pythonPath).then((ready) => {
      if (!cancelled) setPythonReady(ready);
    });
    return () => {
      cancelled = true;
    };
  }, [settings?.pythonPath]);

  useEffect(() => {
    if (!coreSettings || !isLocalWorkflow(workflow)) return;
    void refreshPathStatus(coreSettings);
  }, [coreSettings, workflow]);

  useEffect(() => {
    if (!coreSettings?.local || !isLocalWorkflow(workflow)) {
      setCurrentLlamaState(null);
      return;
    }
    void window.subtitler.getCurrentLlamaServerState(coreSettings.local.llamaServer).then(setCurrentLlamaState);
  }, [coreSettings?.local?.llamaServer, workflow]);

  useEffect(() => {
    if (!settings || !isLocalWorkflow(workflow) || !localProfiles.length) return;
    void refreshLocalModels(settings.modelsDirectory, settings.localModelProfile);
  }, [settings?.modelsDirectory, settings?.localModelProfile, workflow, localProfiles]);

  useEffect(() => {
    if (!settings || !isLocalWorkflow(workflow)) return;
    void refreshManagedLlama(settings.llamaBackend, undefined);
  }, [settings?.llamaBackend, workflow]);

  useEffect(() => {
    if (!coreSettings?.local || !localModelStatus) return;
    const files = localModelStatus.files;
    const profile = localProfiles.find((item) => item.id === settings?.localModelProfile);
    if (
      coreSettings.local.model === files.transcription.path
      && coreSettings.local.mmproj === files.projector.path
        && coreSettings.local.cleanupModel === files.cleanup.path
        && coreSettings.local.transcriptionDraftModel === (files.transcriptionDraft?.path ?? "")
        && coreSettings.local.cleanupDraftModel === (files.cleanupDraft?.path ?? "")
        && coreSettings.cleanupWindowSubtitles === profile?.cleanupWindowSubtitles
    ) return;
    setCoreSettings({
      ...coreSettings,
      cleanupWindowSubtitles: profile?.cleanupWindowSubtitles ?? coreSettings.cleanupWindowSubtitles,
      local: {
        ...coreSettings.local,
        model: files.transcription.path,
        mmproj: files.projector.path,
        cleanupModel: files.cleanup.path,
        transcriptionDraftModel: files.transcriptionDraft?.path ?? "",
        cleanupDraftModel: files.cleanupDraft?.path ?? ""
      }
    });
  }, [localModelStatus, coreSettings, localProfiles, settings?.localModelProfile]);

  useEffect(() => {
    if (runState !== "running" || !startedAt) return;
    const timer = window.setInterval(() => setElapsedMs(Date.now() - startedAt), 500);
    return () => window.clearInterval(timer);
  }, [runState, startedAt]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2400);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!settings || !configs || !coreSettings) return;
    const timer = window.setTimeout(() => {
      void persistWorkflowSettings(false);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [coreSettings, workflow]);

  async function loadInitialState() {
    const appState = await window.subtitler.getAppState();
    setProjectRoot(appState.projectRoot);
    setSettings(appState.settings);
    setConfigs(appState.configs);
    setConfigPaths(appState.configPaths);
    setInputPath(appState.settings.lastInputPath);
    if (appState.settings.lastInputPath) {
      setOutputPath(defaultOutputPath(appState.settings.lastInputPath, appState.settings.selectedWorkflow));
      setSidecarDir(appState.settings.lastSidecarDir || defaultSidecarDir(appState.settings.lastInputPath));
    }
    setGlossary(await window.subtitler.readGlossary());
  }

  async function saveSettings(next = settings, showNotice = true) {
    if (!next) return;
    await window.subtitler.saveAppSettings({ ...next, lastInputPath: inputPath, lastSidecarDir: sidecarDir });
    if (showNotice) setNotice("Settings saved");
  }

  async function persistWorkflowSettings(showNotice = true) {
    if (!settings || !configs || !coreSettings) return;
    const workflowConfig = applyCoreSettings(configs[workflow], coreSettings, workflow);
    if (JSON.stringify(workflowConfig) === JSON.stringify(configs[workflow])) {
      return;
    }
    await window.subtitler.saveWorkflowConfig(workflow, workflowConfig);
    setConfigs({ ...configs, [workflow]: workflowConfig });
    await saveSettings(settings, showNotice);
    if (showNotice) setNotice("Settings saved");
  }

  async function saveGlossary() {
    await window.subtitler.saveGlossary(glossary);
    setNotice("Glossary saved");
  }

  async function refreshPathStatus(core: CoreWorkflowSettings) {
    const local = core.local;
    if (!local) return;
    const entries = {
      model: local.model,
      mmproj: local.mmproj,
      llamaServer: local.llamaServer,
      cleanupModel: local.cleanupModel,
      cleanupLlamaServer: local.cleanupLlamaServer
    };
    const next: Record<string, PathStatus> = {};
    for (const [key, value] of Object.entries(entries)) {
      next[key] = { path: value, exists: Boolean(value) && await window.subtitler.pathExists(value) };
    }
    setPathStatus(next);
  }

  function setWorkflow(nextWorkflow: WorkflowName) {
    if (!settings) return;
    const next = { ...settings, selectedWorkflow: nextWorkflow };
    setSettings(next);
    void saveSettings(next);
  }

  function setEnvFile(path: string) {
    if (!settings) return;
    const next = { ...settings, envFile: path };
    setSettings(next);
    void saveSettings(next);
  }

  function handleInput(path: string) {
    setInputPath(path);
    setAnalysis(null);
    setAnalysisError("");
    if (settings) {
      const next = { ...settings, lastInputPath: path, lastOutputDir: "" };
      setSettings(next);
      void saveSettings(next);
    }
  }

  async function verifyHosted() {
    if (!settings || !coreSettings?.hosted) return;
    setVerifyingHosted(true);
    try {
      const result = await window.subtitler.verifyHostedModels(settings.envFile);
      setHostedVerification(result);
      const transcriptionOptions = [
        result.openai.transcription && { provider: "openai" as const, model: "gpt-4o-transcribe" },
        result.openai.transcriptionMini && { provider: "openai" as const, model: "gpt-4o-mini-transcribe" },
        result.gemini.transcription && { provider: "gemini" as const, model: "gemini-3.5-flash" },
        result.gemini.transcription31Pro && { provider: "gemini" as const, model: "gemini-3.1-pro-preview" },
        result.gemini.transcription31FlashLite && { provider: "gemini" as const, model: "gemini-3.1-flash-lite" }
      ].filter(Boolean) as Array<{ provider: "openai" | "gemini"; model: string }>;
      const cleanupOptions = [
        result.openai.cleanup && { provider: "openai" as const, model: "gpt-5.4-mini" },
        result.openai.cleanup55 && { provider: "openai" as const, model: "gpt-5.5" },
        result.gemini.cleanup && { provider: "gemini" as const, model: "gemini-3.5-flash" },
        result.gemini.cleanup31Pro && { provider: "gemini" as const, model: "gemini-3.1-pro-preview" },
        result.gemini.cleanup31FlashLite && { provider: "gemini" as const, model: "gemini-3.1-flash-lite" }
      ].filter(Boolean) as Array<{ provider: "openai" | "gemini"; model: string }>;
      const hosted = coreSettings.hosted;
      const selectedTranscription = transcriptionOptions.find((option) => option.provider === hosted.transcriptionProvider) ?? transcriptionOptions[0];
      const selectedCleanup = cleanupOptions.find((option) => option.provider === hosted.cleanupProvider) ?? cleanupOptions[0];
      setCoreSettings({
        ...coreSettings,
        hosted: {
          ...hosted,
          transcriptionProvider: selectedTranscription?.provider ?? hosted.transcriptionProvider,
          transcriptionModel: selectedTranscription?.model ?? hosted.transcriptionModel,
          cleanupProvider: selectedCleanup?.provider ?? hosted.cleanupProvider,
          cleanupModel: selectedCleanup?.model ?? hosted.cleanupModel
        }
      });
      setNotice(transcriptionOptions.length && cleanupOptions.length ? "Hosted models verified" : "Model verification completed with unavailable models");
    } finally {
      setVerifyingHosted(false);
    }
  }

  async function refreshLocalModels(modelsDirectory: string, profileId: string) {
    const statuses = await Promise.all(localProfiles.map((profile) => window.subtitler.getLocalModelStatus(modelsDirectory, profile.id)));
    const byProfile = Object.fromEntries(statuses.map((status) => [status.profile, status]));
    setLocalProfileStatuses(byProfile);
    setLocalModelStatus(byProfile[profileId] ?? null);
  }

  async function downloadLocalModels() {
    if (!settings) return;
    setDownloadingModels(true);
    setLogs((value) => `${value}${value && !value.endsWith("\n") ? "\n" : ""}$ Hugging Face model download\n`);
    try {
      const status = await window.subtitler.downloadLocalProfile(settings.modelsDirectory, settings.localModelProfile);
      setLocalModelStatus(status);
      setLocalProfileStatuses((current) => ({ ...current, [status.profile]: status }));
      if (coreSettings?.local) {
        const profile = localProfiles.find((item) => item.id === settings.localModelProfile);
        const next = {
          ...coreSettings,
          cleanupWindowSubtitles: profile?.cleanupWindowSubtitles ?? coreSettings.cleanupWindowSubtitles,
          local: {
            ...coreSettings.local,
            model: status.files.transcription.path,
            mmproj: status.files.projector.path,
            cleanupModel: status.files.cleanup.path,
            transcriptionDraftModel: status.files.transcriptionDraft?.path ?? "",
            cleanupDraftModel: status.files.cleanupDraft?.path ?? ""
          }
        };
        setCoreSettings(next);
      }
      setNotice("Local model profile installed");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadingModels(false);
    }
  }

  async function refreshManagedLlama(backend: LlamaBackendId, releaseTag?: string) {
    const status = await window.subtitler.getManagedLlamaStatus(backend, releaseTag);
    setManagedLlamaStatus(status);
  }

  async function checkLlamaRelease() {
    setLogs((value) => `${value}${value && !value.endsWith("\n") ? "\n" : ""}$ llama.cpp server release check\n`);
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

  async function startRun() {
    if (!settings || !configPaths || !coreSettings) return;
    await persistWorkflowSettings(false);
    setLogs("");
    setRunState("running");
    setElapsedMs(0);
    const result = await window.subtitler.startRun({
      workflow,
      inputPath,
      outputPath,
      configPath: configPaths[workflow],
      envFile: settings.envFile,
      audioTrack: coreSettings.audioTrack,
      sidecarDir: settings.sidecarsEnabled ? sidecarDir : undefined,
      sidecarsEnabled: settings.sidecarsEnabled,
      profile: coreSettings.diagnostics.profile
    });
    setActiveRunId(result.runId);
  }

  async function cancelRun() {
    if (!activeRunId) return;
    await window.subtitler.cancelRun(activeRunId);
    setRunState("cancelled");
  }

  function handleRunEvent(event: RunEvent) {
    if (event.type === "started") {
      setActiveRunId(event.runId);
      setStartedAt(Date.parse(event.startedAt));
      setLogs(`$ ${event.commandPreview}\n`);
    } else if (event.type === "stdout" || event.type === "stderr") {
      setLogs((value) => value + event.text);
    } else if (event.type === "exit") {
      setElapsedMs(event.elapsedMs);
      setRunState(event.cancelled ? "cancelled" : event.code === 0 ? "succeeded" : "failed");
      setActiveRunId("");
    } else if (event.type === "error") {
      setRunState("failed");
      setLogs((value) => `${value}\n${event.message}\n`);
    }
  }

  const elapsed = useMemo(() => formatElapsed(elapsedMs), [elapsedMs]);
  if (!settings || !configs || !configPaths || !coreSettings) {
    return <div className="loading">Loading frontend state...</div>;
  }

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>AviUtl Subtitler</h1>
          <div className="subtle">{projectRoot}</div>
        </div>
        <div className="topbar-controls">
          <ModeSelector workflow={workflow} onChange={setWorkflow} />
          <ThemeSelector value={settings.theme} onChange={(theme) => {
            const next = { ...settings, theme };
            setSettings(next);
            void saveSettings(next);
          }} />
        </div>
      </header>
      <div className="main-grid">
        <div className="left-column">
          <InputPanel
            inputPath={inputPath}
            audioTrack={coreSettings.audioTrack}
            analysis={analysis}
            analyzing={analyzing}
            analysisError={analysisError}
            onInput={handleInput}
            onAudioTrack={(value) => setCoreSettings({ ...coreSettings, audioTrack: value })}
          />
          <OutputPanel
            outputPath={outputPath}
            sidecarDir={sidecarDir}
            sidecarsEnabled={settings.sidecarsEnabled}
            onOutput={setOutputPath}
            onSidecar={setSidecarDir}
            onSidecarsEnabled={(sidecarsEnabled) => {
              const next = { ...settings, sidecarsEnabled };
              setSettings(next);
              void saveSettings(next);
            }}
          />
          <SettingsPanel
            workflow={workflow}
            settings={coreSettings}
            envFile={settings.envFile}
            envStatus={envStatus}
            hostedVerification={hostedVerification}
            verifyingHosted={verifyingHosted}
            pathStatus={pathStatus}
            modelsDirectory={settings.modelsDirectory}
            localModelStatus={localModelStatus}
            localProfiles={localProfiles}
            localProfileStatuses={localProfileStatuses}
            selectedLocalProfile={settings.localModelProfile}
            downloadingModels={downloadingModels}
            llamaBackends={llamaBackends}
            selectedLlamaBackend={settings.llamaBackend}
            llamaRelease={llamaRelease}
            managedLlamaStatus={managedLlamaStatus}
            currentLlamaState={currentLlamaState}
            downloadingLlama={downloadingLlama}
            pythonPath={settings.pythonPath}
            pythonReady={pythonReady}
            onChange={setCoreSettings}
            onPythonPath={(pythonPath) => {
              const next = { ...settings, pythonPath };
              setSettings(next);
              void saveSettings(next);
            }}
            onEnvFile={setEnvFile}
            onVerifyHosted={verifyHosted}
            onModelsDirectory={(modelsDirectory) => {
              const next = { ...settings, modelsDirectory };
              setSettings(next);
              void saveSettings(next);
            }}
            onDownloadLocalModels={downloadLocalModels}
            onLocalProfile={(localModelProfile) => {
              const next = { ...settings, localModelProfile };
              setSettings(next);
              setLocalModelStatus(null);
              void saveSettings(next);
            }}
            onLlamaBackend={(llamaBackend) => {
              const next = { ...settings, llamaBackend };
              setSettings(next);
              setManagedLlamaStatus(null);
              void saveSettings(next);
            }}
            onCheckLlamaRelease={checkLlamaRelease}
            onDownloadLlama={downloadLlama}
            onUseManagedLlama={useManagedLlama}
            onRevertManagedLlama={(path) => useManagedLlama(path)}
          />
        </div>
        <div className="right-column">
          <RunPanel state={runState} elapsed={elapsed} canRun={canRun} onRun={startRun} onCancel={cancelRun} />
          <LogViewer logs={logs} onClear={() => setLogs("")} />
          <GlossaryPanel value={glossary} onChange={setGlossary} onSave={saveGlossary} />
        </div>
      </div>
      {notice && <div className="toast" role="status">{notice}</div>}
    </main>
  );
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function isHostedSelectionVerified(settings: CoreWorkflowSettings | null, verification: HostedModelVerification | null): boolean {
  if (!settings?.hosted || !verification) return false;
  const transcription = settings.hosted.transcriptionProvider === "openai"
    ? (
      (verification.openai.transcription && settings.hosted.transcriptionModel === "gpt-4o-transcribe")
      || (verification.openai.transcriptionMini && settings.hosted.transcriptionModel === "gpt-4o-mini-transcribe")
    )
    : (
      (verification.gemini.transcription && settings.hosted.transcriptionModel === "gemini-3.5-flash")
      || (verification.gemini.transcription31Pro && settings.hosted.transcriptionModel === "gemini-3.1-pro-preview")
      || (verification.gemini.transcription31FlashLite && settings.hosted.transcriptionModel === "gemini-3.1-flash-lite")
    );
  const cleanup = settings.hosted.cleanupProvider === "openai"
    ? (settings.hosted.cleanupModel === "gpt-5.4-mini" ? verification.openai.cleanup : settings.hosted.cleanupModel === "gpt-5.5" && verification.openai.cleanup55)
    : (
      (settings.hosted.cleanupModel === "gemini-3.5-flash" && verification.gemini.cleanup)
      || (settings.hosted.cleanupModel === "gemini-3.1-pro-preview" && verification.gemini.cleanup31Pro)
      || (settings.hosted.cleanupModel === "gemini-3.1-flash-lite" && verification.gemini.cleanup31FlashLite)
    );
  return transcription && cleanup;
}
