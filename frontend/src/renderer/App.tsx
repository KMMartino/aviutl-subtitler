import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { ArrowLeft, Settings as SettingsIcon } from "lucide-react";
import ModeSelector from "./components/ModeSelector";
import ThemeSelector from "./components/ThemeSelector";
import InputPanel from "./components/InputPanel";
import SettingsPanel from "./components/SettingsPanel";
import GlossaryPanel from "./components/GlossaryPanel";
import RunPanel from "./components/RunPanel";
import LogViewer from "./components/LogViewer";
import OutputPanel from "./components/OutputPanel";
import AdditionalSettingsPanel from "./components/AdditionalSettingsPanel";
import SilenceReviewScreen from "./components/SilenceReviewScreen";
import { applyCoreSettings, extractCoreSettings } from "./lib/configPatch";
import { defaultOutputPath, defaultSidecarDir } from "./lib/paths";
import type { AppSettings, CoreWorkflowSettings, CutSilenceEncoderPreset, EncoderProbeResult, PathStatus, RunEvent, RunState, SilenceCutCandidate, SilenceCutDecision, WorkflowConfig, WorkflowName } from "./lib/types";
import { isHostedWorkflow, isLocalWorkflow } from "./lib/workflowLabels";
import { useBatchedLog } from "./hooks/useBatchedLog";
import { useMediaAnalysis } from "./hooks/useMediaAnalysis";
import { useHostedModels } from "./hooks/useHostedModels";
import { useLocalModels } from "./hooks/useLocalModels";
import { useManagedLlama as useManagedLlamaController } from "./hooks/useManagedLlama";
import { useRuntimeSetup } from "./hooks/useRuntimeSetup";
import { clampResize, resizeFromKey } from "./lib/resizeInteraction";
import { defaultSettingsExpansion, updateSettingsExpansion, workflowFamily, type SettingsExpansionByFamily } from "./lib/settingsExpansion";

export default function App() {
  const [startupError, setStartupError] = useState("");
  const pathRequest = useRef(0);
  const [projectRoot, setProjectRoot] = useState("");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [configs, setConfigs] = useState<Record<WorkflowName, WorkflowConfig> | null>(null);
  const [configPaths, setConfigPaths] = useState<Record<WorkflowName, string> | null>(null);
  const [coreSettings, setCoreSettings] = useState<CoreWorkflowSettings | null>(null);
  const [settingsExpansion, setSettingsExpansion] = useState<SettingsExpansionByFamily>({});
  const [inputPath, setInputPath] = useState("");
  const [mediaAnalysisRevision, setMediaAnalysisRevision] = useState(0);
  const [outputPath, setOutputPath] = useState("");
  const [sidecarDir, setSidecarDir] = useState("");
  const [managedDeleteAction, setManagedDeleteAction] = useState("");
  const [pathStatus, setPathStatus] = useState<Record<string, PathStatus>>({});
  const [glossary, setGlossary] = useState("");
  const { logs, append: appendLog, replace: replaceLogs, clear: clearLogs } = useBatchedLog();
  const [runState, setRunState] = useState<RunState>("idle");
  const [activeRunId, setActiveRunId] = useState("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [notice, setNotice] = useState("");
  const [encoderProbes, setEncoderProbes] = useState<EncoderProbeResult[]>([]);
  const [probingEncoders, setProbingEncoders] = useState(false);
  const [silenceReview, setSilenceReview] = useState<{ runId: string; reviewId: string; candidates: SilenceCutCandidate[] } | null>(null);
  const workflow = settings?.selectedWorkflow ?? "local";
  const { envStatus, hostedVerification, verifyingHosted, hostedSelectionReady, verifyHosted } = useHostedModels({ settings, coreSettings, setCoreSettings, setNotice });
  const {
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
  } = useLocalModels({ settings, coreSettings, setCoreSettings, appendLog, setNotice, setManagedDeleteAction });
  const {
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
  } = useManagedLlamaController({ settings, coreSettings, configs, workflow, setCoreSettings, setConfigs, setManagedDeleteAction, appendLog, setNotice, refreshPathStatus });
  const {
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
  } = useRuntimeSetup({ appendLog, setNotice, setSettings, setConfigs, setCoreSettings, refreshHfDownloaderStatus });
  const { analysis, analyzing, analysisError, clearAnalysis } = useMediaAnalysis(inputPath, mediaAnalysisRevision, setCoreSettings);
  const [view, setView] = useState<"main" | "settings">("main");
  const [inputWidth, setInputWidth] = useState(48);
  const [logsHeight, setLogsHeight] = useState(24);

  const hostedReady = !isHostedWorkflow(workflow) || hostedSelectionReady;
  const localReady = !isLocalWorkflow(workflow) || Boolean(localModelStatus?.installed && pathStatus.llamaServer?.exists);
  const ffmpegReady = Boolean(runtimeStatus?.ffmpeg.ready);
  const pythonRequirementsReady = Boolean(runtimeStatus?.python.requirementsInstalled);
  const alignmentReady = Boolean(
    coreSettings?.alignment
    && (!coreSettings.alignment.offlineModelCache
      || (runtimeStatus?.alignment.installed && coreSettings.alignment.model === runtimeStatus.alignment.modelPath))
  );
  const cutSilenceEnabled = (coreSettings?.additionalSettings?.cutSilenceMode ?? "off") !== "off";
  const renderCutVideo = cutSilenceEnabled && Boolean(coreSettings?.additionalSettings?.renderCutVideo);
  const selectedEncoderProbe = encoderProbes.find((probe) => probe.preset === settings?.cutSilenceEncoderPreset);
  const cutSilenceReady = !cutSilenceEnabled || Boolean(
    analysis?.videoCodec
    && (!renderCutVideo || (settings?.cutSilenceEncoderPreset !== "unconfigured" && selectedEncoderProbe?.available && !probingEncoders))
  );
  const canRun = Boolean(settings && configs && configPaths && inputPath && outputPath && pythonReady && pythonRequirementsReady && ffmpegReady && alignmentReady && hostedReady && localReady && cutSilenceReady);

  useEffect(() => {
    void loadInitialState();
    void window.subtitler.listLocalProfiles().then(setLocalProfiles);
    void window.subtitler.listLlamaBackends().then(setLlamaBackends);
    void refreshHfDownloaderStatus();
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
    if (!settings) return;
    let cancelled = false;
    void refreshRuntimeStatus().then((status) => {
      if (!cancelled && status) setPythonReady(status.python.ready && status.python.requirementsInstalled);
    });
    void refreshHfDownloaderStatus();
    return () => {
      cancelled = true;
    };
  }, [settings?.pythonPath]);

  useEffect(() => {
    if (!runtimeStatus?.ffmpeg.ready) { setEncoderProbes([]); return; }
    void probeEncoders();
  }, [runtimeStatus?.ffmpeg.ffmpegPath, runtimeStatus?.ffmpeg.version]);

  useEffect(() => {
    if (!coreSettings || !isLocalWorkflow(workflow)) return;
    void refreshPathStatus(coreSettings);
  }, [coreSettings, workflow]);

  useEffect(() => {
    if (!coreSettings?.local || !isLocalWorkflow(workflow)) {
      setCurrentLlamaState(null);
      return;
    }
    let current = true;
    const serverPath = coreSettings.local.llamaServer;
    void window.subtitler.getCurrentLlamaServerState(serverPath).then((state) => {
      if (current && coreSettings.local?.llamaServer === serverPath) setCurrentLlamaState(state);
    });
    return () => { current = false; };
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
    if ((runState !== "running" && runState !== "reviewing") || !startedAt) return;
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
      void persistWorkflowSettings(false).catch((error) => setNotice(`Settings were not saved: ${error instanceof Error ? error.message : String(error)}`));
    }, 500);
    return () => window.clearTimeout(timer);
  }, [coreSettings, workflow]);

  async function loadInitialState() {
    try {
      const appState = await window.subtitler.getAppState();
      setStartupError("");
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
    } catch (error) {
      setStartupError(error instanceof Error ? error.message : String(error));
    }
  }

  async function saveSettings(next = settings, showNotice = true) {
    if (!next) return;
    try {
      await window.subtitler.saveAppSettings({ ...next, lastSidecarDir: sidecarDir });
      if (showNotice) setNotice("Settings saved");
    } catch (error) {
      setNotice(`Settings were not saved: ${error instanceof Error ? error.message : String(error)}`);
    }
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

  async function importGlossary() {
    const imported = await window.subtitler.importGlossary();
    if (imported === null) return;
    setGlossary(imported);
    setNotice("Glossary imported");
  }

  async function refreshPathStatus(core: CoreWorkflowSettings) {
    const request = ++pathRequest.current;
    const local = core.local;
    if (!local) return;
    const entries = {
      model: local.model,
      mmproj: local.mmproj,
      llamaServer: local.llamaServer,
      cleanupModel: local.cleanupModel,
      cleanupLlamaServer: local.cleanupLlamaServer
    };
    const checked = await Promise.all(Object.entries(entries).map(async ([key, value]) => [key, { path: value, exists: Boolean(value) && await window.subtitler.pathExists(value) }] as const));
    if (request !== pathRequest.current) return;
    setPathStatus(Object.fromEntries(checked));
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
    // A file dialog can return the current path again. React will not rerun an
    // inputPath-dependent effect for the same string, so explicitly identify
    // every user selection as a fresh analysis request.
    setMediaAnalysisRevision((revision) => revision + 1);
    clearAnalysis();
    if (settings) {
      const next = { ...settings, lastInputPath: path, lastOutputDir: "" };
      setSettings(next);
      void saveSettings(next);
    }
  }

  async function startRun() {
    if (!settings || !configPaths || !coreSettings) return;
    try {
      await persistWorkflowSettings(false);
      clearLogs();
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
        profile: coreSettings.diagnostics.profile,
        cutSilenceEncoderPreset: settings.cutSilenceEncoderPreset,
        silencePreviewHeight: settings.silencePreviewHeight,
        silencePreviewFps: settings.silencePreviewFps
      });
      setActiveRunId(result.runId);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setRunState("failed");
      setActiveRunId("");
      replaceLogs(message ? `${message}\n` : "");
      setNotice(message || "Run failed to start");
    }
  }

  async function cancelRun() {
    if (!activeRunId) return;
    await window.subtitler.cancelRun(activeRunId);
  }

  function handleRunEvent(event: RunEvent) {
    if (event.type === "started") {
      setActiveRunId(event.runId);
      setStartedAt(Date.parse(event.startedAt));
      replaceLogs(`$ ${event.commandPreview}\n`);
    } else if (event.type === "stdout" || event.type === "stderr") {
      appendLog(event.text);
    } else if (event.type === "exit") {
      setElapsedMs(event.elapsedMs);
      setRunState(event.cancelled ? "cancelled" : event.code === 0 ? "succeeded" : "failed");
      setActiveRunId("");
      setSilenceReview(null);
    } else if (event.type === "error") {
      setRunState("failed");
      appendLog(`\n${event.message}\n`);
    } else if (event.type === "silence-review-required") {
      setRunState("reviewing");
      setSilenceReview({ runId: event.runId, reviewId: event.reviewId, candidates: event.candidates });
    } else if (event.type === "silence-cut-output") {
      appendLog(`\nCut video: ${event.path}\n`);
      setNotice("Cut video created");
    } else if (event.type === "silence-candidates" && event.workflow === "hosted" && event.candidates.length) {
      void preflightHostedSilencePreview(event.runId, event.candidates);
    }
  }

  async function preflightHostedSilencePreview(runId: string, candidates: SilenceCutCandidate[]) {
    try {
      const source = await window.subtitler.getSilenceSource(runId);
      const video = document.createElement("video");
      video.preload = "metadata";
      const supported = await new Promise<boolean>((resolve) => {
        const timer = window.setTimeout(() => resolve(false), 5000);
        video.onloadedmetadata = () => { window.clearTimeout(timer); resolve(true); };
        video.onerror = () => { window.clearTimeout(timer); resolve(false); };
        video.src = source.url;
      });
      video.removeAttribute("src");
      video.load();
      if (!supported) await window.subtitler.prefetchSilenceProxies(runId, candidates.slice(0, 2).map((candidate) => candidate.id));
    } catch { /* Preview fallback is retried on the review screen. */ }
  }

  async function probeEncoders() {
    setProbingEncoders(true);
    try { setEncoderProbes(await window.subtitler.probeCutSilenceEncoders()); }
    catch (error) { setNotice(`Encoder check failed: ${error instanceof Error ? error.message : String(error)}`); }
    finally { setProbingEncoders(false); }
  }

  function updateMachineSettings(patch: Partial<AppSettings>) {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    void saveSettings(next, false);
  }

  async function submitSilenceReview(decisions: Array<{ candidateId: string; decision: SilenceCutDecision }>) {
    if (!silenceReview) return;
    await window.subtitler.submitSilenceReview(silenceReview.runId, silenceReview.reviewId, decisions);
    setSilenceReview(null);
    setRunState("running");
  }

  function openCutSilenceSettings() {
    setView("settings");
    const family = workflowFamily(workflow);
    setSettingsExpansion((current) => ({ ...current, [family]: { ...(current[family] ?? currentSettingsExpansion), cutSilence: true } }));
  }

  function startColumnResize(event: PointerEvent<HTMLDivElement>) {
    const container = event.currentTarget.parentElement;
    if (!container) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const rect = container.getBoundingClientRect();
    const move = (moveEvent: globalThis.PointerEvent) => {
      const percent = ((moveEvent.clientX - rect.left) / rect.width) * 100;
      setInputWidth(clampResize(percent, 38, 72));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  }

  function startLogResize(event: PointerEvent<HTMLDivElement>) {
    const container = event.currentTarget.parentElement;
    if (!container) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const rect = container.getBoundingClientRect();
    const move = (moveEvent: globalThis.PointerEvent) => {
      const percent = ((rect.bottom - moveEvent.clientY) / rect.height) * 100;
      setLogsHeight(clampResize(percent, 14, 48));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
  }

  function resizeWithKeyboard(event: KeyboardEvent<HTMLDivElement>, value: number, orientation: "vertical" | "horizontal", minimum: number, maximum: number, update: (next: number) => void) {
    const next = resizeFromKey(value, event.key, orientation, minimum, maximum, event.shiftKey);
    if (next === null) return;
    event.preventDefault();
    update(next);
  }

  const elapsed = useMemo(() => formatElapsed(elapsedMs), [elapsedMs]);
  if (startupError) {
    return <div className="loading" role="alert"><p>SubUtl could not load its saved state.</p><p>{startupError}</p><button onClick={() => void loadInitialState()}>Try again</button><button onClick={async () => { await window.subtitler.resetAppState(); await loadInitialState(); }}>Reset saved settings</button></div>;
  }
  if (!settings || !configs || !configPaths || !coreSettings) {
    return <div className="loading">Loading frontend state...</div>;
  }

  if (silenceReview) return <SilenceReviewScreen runId={silenceReview.runId} reviewId={silenceReview.reviewId} candidates={silenceReview.candidates} onSubmit={submitSilenceReview} onCancel={() => void cancelRun()} />;

  const currentWorkflowFamily = workflowFamily(workflow);
  const currentSettingsExpansion = settingsExpansion[currentWorkflowFamily] ?? defaultSettingsExpansion({
    pythonReady,
    ffmpegReady: Boolean(runtimeStatus?.ffmpeg.ready),
    alignmentInstalled: Boolean(runtimeStatus?.alignment.installed),
    envExists: envStatus.exists,
    serverExists: Boolean(pathStatus.llamaServer?.exists),
  });

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>AviUtl Subtitler</h1>
          <div className="subtle">{projectRoot}</div>
        </div>
        <div className="topbar-controls">
          <ModeSelector workflow={workflow} onChange={setWorkflow} disabled={runState === "running"} />
          <ThemeSelector value={settings.theme} onChange={(theme) => {
            const next = { ...settings, theme };
            setSettings(next);
            void saveSettings(next);
          }} />
          <button className="topbar-button" onClick={() => setView(view === "settings" ? "main" : "settings")}>
            {view === "settings" ? <ArrowLeft size={16} /> : <SettingsIcon size={16} />}
            {view === "settings" ? "Back" : "Settings"}
          </button>
        </div>
      </header>
      {view === "settings" ? (
        <div className="settings-view">
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
            deletingManaged={managedDeleteAction}
            modelDownloadMode={settings.modelDownloadMode ?? "direct"}
            hfDownloaderStatus={hfDownloaderStatus}
            installingHfDownloader={installingHfDownloader}
            llamaBackends={llamaBackends}
            selectedLlamaBackend={settings.llamaBackend}
            llamaRelease={llamaRelease}
            managedLlamaStatus={managedLlamaStatus}
            currentLlamaState={currentLlamaState}
            downloadingLlama={downloadingLlama}
            pythonPath={settings.pythonPath}
            pythonReady={pythonReady}
            runtimeStatus={runtimeStatus}
            runtimeAction={runtimeAction}
            runtimeFeedback={runtimeFeedback}
            sidecarsEnabled={settings.sidecarsEnabled}
            sidecarDir={sidecarDir}
            outputPath={outputPath}
            runActive={runState === "running"}
            expansion={currentSettingsExpansion}
            onToggleExpansion={(section) => setSettingsExpansion((current) => ({
              ...current,
              [currentWorkflowFamily]: updateSettingsExpansion(current[currentWorkflowFamily] ?? currentSettingsExpansion, section),
            }))}
            onChange={setCoreSettings}
            onPythonPath={(pythonPath) => {
              const next = { ...settings, pythonPath };
              setSettings(next);
              void saveSettings(next);
            }}
            onEnvFile={setEnvFile}
            onSidecar={setSidecarDir}
            onSidecarsEnabled={(sidecarsEnabled) => {
              const next = { ...settings, sidecarsEnabled };
              setSettings(next);
              void saveSettings(next);
            }}
            onVerifyHosted={verifyHosted}
            onModelsDirectory={(modelsDirectory) => {
              const next = { ...settings, modelsDirectory };
              setSettings(next);
              void saveSettings(next);
            }}
            onDownloadLocalModels={downloadLocalModels}
            onDeleteLocalModels={deleteLocalModels}
            onModelDownloadMode={(modelDownloadMode) => {
              const next = { ...settings, modelDownloadMode };
              setSettings(next);
              void saveSettings(next);
            }}
            onInstallHfDownloader={installHfDownloader}
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
            onDeleteLlama={deleteManagedLlama}
            onUseManagedLlama={useManagedLlama}
            onRevertManagedLlama={(path) => useManagedLlama(path)}
            onRefreshRuntime={refreshRuntimeStatus}
            onCreateManagedPython={createManagedPythonEnv}
            onInstallPythonRequirements={installPythonRequirements}
            onDeleteManagedPython={deleteManagedPythonEnv}
            onDownloadFfmpeg={downloadFfmpeg}
            onDeleteFfmpeg={deleteManagedFfmpeg}
            onDownloadAlignment={downloadAlignmentModel}
            onDeleteAlignment={deleteManagedAlignmentModel}
            cutSilenceEncoderPreset={settings.cutSilenceEncoderPreset}
            silencePreviewHeight={settings.silencePreviewHeight}
            silencePreviewFps={settings.silencePreviewFps}
            encoderProbes={encoderProbes}
            probingEncoders={probingEncoders}
            onCutSilenceEncoder={(cutSilenceEncoderPreset: CutSilenceEncoderPreset) => updateMachineSettings({ cutSilenceEncoderPreset })}
            onSilencePreviewHeight={(silencePreviewHeight) => updateMachineSettings({ silencePreviewHeight })}
            onSilencePreviewFps={(silencePreviewFps) => updateMachineSettings({ silencePreviewFps })}
            onProbeEncoders={() => void probeEncoders()}
          />
        </div>
      ) : (
      <div className="main-workspace" style={{ "--logs-height": `${logsHeight}%` } as React.CSSProperties}>
        <div className="primary-flow" style={{ "--input-width": `${inputWidth}%` } as React.CSSProperties}>
          <div className="input-stack">
            <InputPanel
              inputPath={inputPath}
              audioTrack={coreSettings.audioTrack}
              analysis={analysis}
              analyzing={analyzing}
              analysisError={analysisError}
              disabled={runState === "running"}
              onInput={handleInput}
              onAudioTrack={(value) => setCoreSettings({ ...coreSettings, audioTrack: value })}
            />
            <RunPanel state={runState} elapsed={elapsed} canRun={canRun} onRun={startRun} onCancel={cancelRun} />
          </div>
          <div className="resize-divider column-divider" role="separator" aria-label="Resize input and settings panels" aria-orientation="vertical" aria-valuemin={38} aria-valuemax={72} aria-valuenow={Math.round(inputWidth)} aria-valuetext={`${Math.round(inputWidth)}% input width`} tabIndex={0} title="Drag or use arrow keys to resize input and right panels" onPointerDown={startColumnResize} onKeyDown={(event) => resizeWithKeyboard(event, inputWidth, "vertical", 38, 72, setInputWidth)} />
          <div className="flow-side">
          <OutputPanel
            outputPath={outputPath}
            disabled={runState === "running"}
            onOutput={setOutputPath}
          />
           <AdditionalSettingsPanel workflow={workflow} settings={coreSettings} encoder={settings.cutSilenceEncoderPreset} encoderReady={Boolean(selectedEncoderProbe?.available) && !probingEncoders} encoderChecking={probingEncoders} hasVideo={Boolean(analysis?.videoCodec)} frameRateMode={analysis?.frameRateMode ?? "unknown"} disabled={runState === "running"} onConfigure={openCutSilenceSettings} onChange={setCoreSettings} />
          <GlossaryPanel value={glossary} onChange={setGlossary} onSave={saveGlossary} onImport={importGlossary} />
          </div>
        </div>
        <div className="resize-divider log-divider" role="separator" aria-label="Resize log panel" aria-orientation="horizontal" aria-valuemin={14} aria-valuemax={48} aria-valuenow={Math.round(logsHeight)} aria-valuetext={`${Math.round(logsHeight)}% log height`} tabIndex={0} title="Drag or use arrow keys to resize logs" onPointerDown={startLogResize} onKeyDown={(event) => resizeWithKeyboard(event, logsHeight, "horizontal", 14, 48, setLogsHeight)} />
        <div className="logs-row">
          <LogViewer logs={logs} onClear={clearLogs} />
        </div>
      </div>
      )}
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
