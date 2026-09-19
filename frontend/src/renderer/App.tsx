import MomentExtractionPanel, { type MomentDraft, type MomentExtractionHandle } from "./components/MomentExtractionPanel";
import type { SourceDownload } from "./components/SourceUrlInput";
import type { MomentExtractionRequest } from "./lib/types";
import CreatorWorkspace from "./components/CreatorWorkspace";
import { speechPath } from "../shared/creatorProject";
import type { CreatorProject, ProjectResult } from "../shared/creatorProject";
import { buildEditorialSources } from "./lib/editorialPairing";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { ArrowLeft, Library, Settings as SettingsIcon } from "lucide-react";
import ModeSelector from "./components/ModeSelector";
import ThemeSelector from "./components/ThemeSelector";
import InputPanel from "./components/InputPanel";
import SilenceProjectPanel from "./components/SilenceProjectPanel";
import SettingsPanel from "./components/SettingsPanel";
import GlossaryPanel from "./components/GlossaryPanel";
import RunPanel from "./components/RunPanel";
import LogViewer from "./components/LogViewer";
import OutputPanel from "./components/OutputPanel";
import AdditionalSettingsPanel from "./components/AdditionalSettingsPanel";
import SilenceReviewScreen from "./components/SilenceReviewScreen";
import MediaLibraryScreen from "./components/MediaLibraryScreen";
import BrollReviewScreen from "./components/BrollReviewScreen";
import { applyCoreSettings, extractCoreSettings } from "./lib/configPatch";
import { defaultEditorialCheckpointPath, defaultOutputPath, defaultSidecarDir, joinPath } from "./lib/paths";
import type { AppSettings, BrollCandidate, BrollReviewDecision, CoreWorkflowSettings, CutSilenceEncoderPreset, EditorialCutApplicationResult, EditorialProjectRequest, EditorialRestartMode, EncoderProbeResult, PathStatus, RunEvent, RunState, SilenceCutCandidate, SilenceCutDecision, WorkflowConfig, WorkflowName } from "./lib/types";
import { isLocalWorkflow } from "../shared/workflowCatalog";
import { useBatchedLog } from "./hooks/useBatchedLog";
import { useMediaAnalysis } from "./hooks/useMediaAnalysis";
import { useHostedModels } from "./hooks/useHostedModels";
import { useLocalModels } from "./hooks/useLocalModels";
import { useManagedLlama as useManagedLlamaController } from "./hooks/useManagedLlama";
import { useRuntimeSetup } from "./hooks/useRuntimeSetup";
import { clampResize, resizeFromKey } from "./lib/resizeInteraction";
import { defaultSettingsExpansion, updateSettingsExpansion, workflowFamily, type SettingsExpansionByFamily } from "./lib/settingsExpansion";
import { useI18n } from "./i18n";

export default function App() {
  const { setLocale, t } = useI18n();
  const [creatorProject, setCreatorProject] = useState<CreatorProject | null>(null);
  const creatorProjectRef = useRef<CreatorProject | null>(null);
  const projectSaveQueue = useRef(Promise.resolve());
  const [projectSaving, setProjectSaving] = useState(false);
  const [extraction, setExtraction] = useState(false);
  const momentPanel = useRef<MomentExtractionHandle>(null);
  const [subtitleDownload, setSubtitleDownload] = useState<SourceDownload>({ url: "", directory: "" });
  const [momentDownload, setMomentDownload] = useState<SourceDownload>({ url: "", directory: "" });
  const [defaultProjectDirectory, setDefaultProjectDirectory] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<number | null>(null);
  const downloadCancelled = useRef(false);
  useEffect(() => {
    void window.subtitler.projectCatalog().then(catalog => setDefaultProjectDirectory(catalog.defaultDirectory))
      .catch((error: unknown) => setNotice(String(error)));
    return window.subtitler.onSourceProgress(setDownloadProgress);
  }, []);
  const momentDraft = useRef<MomentDraft | null>(null);
  const onMomentDraft = useCallback((value: MomentDraft) => { momentDraft.current = value; }, []);
  const [momentSpec, setMomentSpec] = useState<MomentExtractionRequest | null>(null);
  const [momentAudioTrack, setMomentAudioTrack] = useState(0);
  const [momentOutput, setMomentOutput] = useState("");
  const [ytDlpOutdated, setYtDlpOutdated] = useState(false);
  const [ytDlpUpdating, setYtDlpUpdating] = useState(false);
  const lastSubtitleWorkflow = useRef<WorkflowName>("hosted");
  const onMomentReady = useCallback((value: MomentExtractionRequest | null, track: number) => {
    setMomentSpec(value); setMomentAudioTrack(track);
  }, []);
  useEffect(() => window.subtitler.onYtDlpOutdated(() => setYtDlpOutdated(true)), []);
  function changeEditorialProject(value: EditorialProjectRequest) {
    setEditorialProject(value);
    const directory = creatorProjectRef.current?.directory;
    if (!directory) return;
    setProjectSaving(true);
    projectSaveQueue.current = projectSaveQueue.current.then(async () => {
      const project = creatorProjectRef.current;
      if (!project || project.directory !== directory) return;
      const recordings = value.sources.map((source) => ({ id: project.recordings.find((recording) => recording.source.visualPath === source.visualPath)?.id ?? crypto.randomUUID(), source }));
      const saved = await window.subtitler.updateProject({ ...project, recordings, editorial: value });
      creatorProjectRef.current = saved; setCreatorProject(saved);
    }).catch((error: unknown) => setNotice(String(error)));
    const pending = projectSaveQueue.current;
    void pending.finally(() => { if (pending === projectSaveQueue.current) setProjectSaving(false); });
  }
  const [creatorRecordingId, setCreatorRecordingId] = useState("");
  function receiveProject(project: CreatorProject | null) {
    creatorProjectRef.current = project;
    setCreatorProject(project);
    if (project) {
      setEditorialProject({ ...project.editorial, sources: project.recordings.map((recording) => recording.source) });
      const recording = project.recordings.find((recording) => recording.id === creatorRecordingId) ?? project.recordings[0];
      setCreatorRecordingId(recording?.id ?? "");
      setInputPath(recording ? (settings?.selectedWorkflow === "hosted-long-stream" ? recording.source.visualPath : speechPath(recording.source)) : "");
    } else {
      setCreatorRecordingId(""); setInputPath("");
      setEditorialProject({ sources: [], titleOrGame: "", objective: "", targetDurationMinSeconds: 60, targetDurationMaxSeconds: 60, outputLocale: "en" });
    }
    setEditorialResumeCheckpoint(""); setEditorialExtensionCheckpoint(""); setReviewedEditorialProject("");
  }
  const [startupError, setStartupError] = useState("");
  const pathRequest = useRef(0);
  const [projectRoot, setProjectRoot] = useState("");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  useEffect(() => { if (settings?.selectedWorkflow && settings.selectedWorkflow !== "hosted-long-stream") lastSubtitleWorkflow.current = settings.selectedWorkflow; }, [settings?.selectedWorkflow]);
  const [configs, setConfigs] = useState<Record<WorkflowName, WorkflowConfig> | null>(null);
  const [configPaths, setConfigPaths] = useState<Record<WorkflowName, string> | null>(null);
  const [coreSettings, setCoreSettings] = useState<CoreWorkflowSettings | null>(null);
  const [settingsExpansion, setSettingsExpansion] = useState<SettingsExpansionByFamily>({});
  const [inputPath, setInputPath] = useState("");
  const [mediaAnalysisRevision, setMediaAnalysisRevision] = useState(0);
  const [outputPath, setOutputPath] = useState("");
  const [sidecarDir, setSidecarDir] = useState("");
  const [editorialProject, setEditorialProject] = useState<EditorialProjectRequest>({
    sources: [],
    titleOrGame: "",
    objective: "",
    targetDurationMinSeconds: 60,
    targetDurationMaxSeconds: 60,
    outputLocale: "en"
  });
  const [editorialResumeCheckpoint, setEditorialResumeCheckpoint] = useState("");
  const [editorialRestartFrom] = useState<EditorialRestartMode>("compatible");
  const [editorialExtensionCheckpoint, setEditorialExtensionCheckpoint] = useState("");
  const [reviewedEditorialProject, setReviewedEditorialProject] = useState("");
  const [, setEditorialCutApplication] = useState<EditorialCutApplicationResult | null>(null);
  const [managedDeleteAction, setManagedDeleteAction] = useState("");
  const [pathStatus, setPathStatus] = useState<Record<string, PathStatus>>({});
  const [glossary, setGlossary] = useState("");
  const { logs, append: appendLog, replace: replaceLogs, clear: clearLogs } = useBatchedLog();
  const [acquiringSource, setAcquiringSource] = useState(false);
  const [runState, setRunState] = useState<RunState>("idle");
  const [activeRunId, setActiveRunId] = useState("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [notice, setNotice] = useState("");
  const [encoderProbes, setEncoderProbes] = useState<EncoderProbeResult[]>([]);
  const [probingEncoders, setProbingEncoders] = useState(false);
  const [silenceReview, setSilenceReview] = useState<{ runId: string; reviewId: string; candidates: SilenceCutCandidate[] } | null>(null);
  const [brollReview, setBrollReview] = useState<{ runId: string; reviewId: string; candidates: BrollCandidate[] } | null>(null);
  const workflow = extraction ? "hosted-long-stream" : settings?.selectedWorkflow ?? "local";
  const { envStatus, envStatusLoaded, hostedVerification, verifyingHosted, hostedSelectionReady, verifyHosted } = useHostedModels({ settings, coreSettings, setCoreSettings, setNotice });
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
    installOrUpdateYtDlp,
    deleteManagedYtDlp,
    downloadAlignmentModel,
    deleteManagedAlignmentModel,
  } = useRuntimeSetup({ appendLog, setNotice, setSettings, setConfigs, setCoreSettings, refreshHfDownloaderStatus });
  const { analysis, analyzing, analysisError, clearAnalysis } = useMediaAnalysis(inputPath, mediaAnalysisRevision, setCoreSettings);
  const [view, setView] = useState<"main" | "settings" | "library">("main");
  const [libraryVisited, setLibraryVisited] = useState(false);
  const [inputWidth, setInputWidth] = useState(48);
  const [logsHeight, setLogsHeight] = useState(24);

  const hostedReady = workflow !== "hosted" || hostedSelectionReady;
  const localReady = workflow !== "local" || Boolean(localModelStatus?.installed && pathStatus.llamaServer?.exists);
  const ffmpegReady = Boolean(runtimeStatus?.ffmpeg.ready);
  const pythonRequirementsReady = Boolean(runtimeStatus?.python.requirementsInstalled);
  const alignmentReady = (workflow === "hosted-long-stream" && !extraction) || Boolean(
    coreSettings?.alignment
    && (!coreSettings.alignment.offlineModelCache
      || (runtimeStatus?.alignment.installed && coreSettings.alignment.model === runtimeStatus.alignment.modelPath))
  );
  const cutSilenceEnabled = workflow !== "hosted-long-stream" && (coreSettings?.additionalSettings?.cutSilenceMode ?? "off") !== "off";
  const renderCutVideo = cutSilenceEnabled && Boolean(coreSettings?.additionalSettings?.renderCutVideo);
  const selectedEncoderProbe = encoderProbes.find((probe) => probe.preset === settings?.cutSilenceEncoderPreset);
  const cutSilenceReady = !cutSilenceEnabled || Boolean(
    analysis?.videoCodec
    && (!renderCutVideo || (settings?.cutSilenceEncoderPreset !== "unconfigured" && selectedEncoderProbe?.available && !probingEncoders))
  );
  const editorialMapEnabled = workflow === "hosted-long-stream";
  const editorialReady = !editorialMapEnabled || Boolean(
    editorialResumeCheckpoint || (editorialProject.sources.length
    && editorialProject.sources.every((source) => source.roleConfirmed)
)
  );
  const projectDownloadLocation = creatorProject ? joinPath(creatorProject.directory, "Sources") : t("input.newProjectDownload", { path: defaultProjectDirectory });
  const pendingDownload = extraction ? momentDownload : subtitleDownload;
  const downloadRequested = (extraction || !editorialMapEnabled) && Boolean(pendingDownload.url.trim());
  const downloadReady = !downloadRequested || Boolean(/^https?:\/\/\S+$/i.test(pendingDownload.url.trim()));
  const canRun = downloadReady && (extraction ? Boolean(momentSpec && !acquiringSource && pythonReady && pythonRequirementsReady && ffmpegReady && envStatus.keysPresent.OPENAI_API_KEY && (configs?.["hosted-long-stream"].backend?.transcriber !== "gemini" || envStatus.keysPresent.GEMINI_API_KEY) && alignmentReady) : Boolean(!projectSaving && !acquiringSource && settings && configs && configPaths && pythonReady && pythonRequirementsReady && hostedReady && localReady && (
    reviewedEditorialProject
      ? editorialMapEnabled
      : (downloadRequested || inputPath || editorialResumeCheckpoint || editorialExtensionCheckpoint) && (downloadRequested || outputPath) && ffmpegReady && alignmentReady && (downloadRequested ? (!renderCutVideo || Boolean(selectedEncoderProbe?.available)) : cutSilenceReady) && editorialReady
  )));

  const hostedProviders = coreSettings?.hosted ? [coreSettings.hosted.transcriptionProvider, coreSettings.hosted.fallbackTranscriptionProvider, coreSettings.hosted.cleanupProvider] : [];
  const missingHostedKey = envStatusLoaded && (extraction
    ? !envStatus.keysPresent.OPENAI_API_KEY || (configs?.["hosted-long-stream"].backend?.transcriber === "gemini" && !envStatus.keysPresent.GEMINI_API_KEY)
    : workflow === "hosted" && hostedProviders.some(provider => !envStatus.keysPresent[provider === "openai" ? "OPENAI_API_KEY" : "GEMINI_API_KEY"]));
  const runBlockedReason = canRun ? ""
    : runtimeStatus && (!runtimeStatus.python.ready || !runtimeStatus.python.requirementsInstalled) ? t("run.pythonBlocked")
    : missingHostedKey ? t("run.hostedBlocked")
    : workflow === "local" && (localModelStatus?.installed === false || pathStatus.llamaServer?.exists === false) ? t("run.localBlocked")
    : !reviewedEditorialProject && runtimeStatus?.ffmpeg.ready === false ? t("run.ffmpegBlocked")
    : !reviewedEditorialProject && (extraction || !editorialMapEnabled) && coreSettings?.alignment?.offlineModelCache && runtimeStatus && !alignmentReady ? t("run.alignmentBlocked")
    : renderCutVideo && !probingEncoders && selectedEncoderProbe?.available === false ? t("run.encoderBlocked")
    : "";

  useEffect(() => {
    void loadInitialState();
    void window.subtitler.listLocalProfiles().then(setLocalProfiles);
    void window.subtitler.listLlamaBackends().then(setLlamaBackends);
    void refreshHfDownloaderStatus();
    return window.subtitler.onRunEvent(handleRunEvent);
  }, []);

  useEffect(() => {
    if (!settings || !configs) return;
    setCoreSettings(extractCoreSettings(configs[workflow]));
  }, [workflow, configs]);

  useEffect(() => {
    if (settings) document.documentElement.dataset.theme = settings.theme;
  }, [settings?.theme]);

  useEffect(() => {
    if (!settings) return;
    setLocale(settings.appLocale);
    document.documentElement.lang = settings.appLocale;
  }, [settings?.appLocale, setLocale]);

  useEffect(() => {
    if (!inputPath || !settings) return;
    setOutputPath(editorialResumeCheckpoint || editorialExtensionCheckpoint || (editorialMapEnabled ? defaultEditorialCheckpointPath(inputPath) : defaultOutputPath(inputPath, settings.selectedWorkflow)));
    setSidecarDir(defaultSidecarDir(inputPath));
  }, [inputPath, settings?.selectedWorkflow, editorialMapEnabled, editorialResumeCheckpoint, editorialExtensionCheckpoint]);

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
      void persistWorkflowSettings(false).catch((error) => setNotice(t("notice.settingsSaveFailed", { error: error instanceof Error ? error.message : String(error) })));
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
      if (showNotice) setNotice(t("notice.settingsSaved"));
    } catch (error) {
      setNotice(t("notice.settingsSaveFailed", { error: error instanceof Error ? error.message : String(error) }));
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
    if (showNotice) setNotice(t("notice.settingsSaved"));
  }

  async function saveGlossary() {
    await window.subtitler.saveGlossary(glossary);
    setNotice(t("notice.glossarySaved"));
  }

  async function importGlossary() {
    const imported = await window.subtitler.importGlossary();
    if (imported === null) return;
    setGlossary(imported);
    setNotice(t("notice.glossaryImported"));
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
    if (settings.selectedWorkflow !== "hosted-long-stream") lastSubtitleWorkflow.current = settings.selectedWorkflow;
    if (nextWorkflow !== "hosted-long-stream") lastSubtitleWorkflow.current = nextWorkflow;
    if (creatorProject) {
      setEditorialProject({ ...creatorProject.editorial, sources: creatorProject.recordings.map((recording) => recording.source) });
      const recording = creatorProject.recordings.find((recording) => recording.id === creatorRecordingId) ?? creatorProject.recordings[0];
      if (recording) { setCreatorRecordingId(recording.id); handleInput(nextWorkflow === "hosted-long-stream" ? recording.source.visualPath : speechPath(recording.source)); }
    }
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

  async function resumeProjectResult(result: ProjectResult) {
    if (!creatorProject || !settings || !configPaths) return;
    setWorkflow(result.workflow);
    clearLogs(); setRunState("running"); setElapsedMs(0);
    try {
      const started = await window.subtitler.startRun({
        creatorProjectDirectory: creatorProject.directory, creatorResumeResultId: result.id,
        workflow: result.workflow, inputPath: result.outputPath, outputPath: result.outputPath,
        configPath: configPaths[result.workflow], envFile: settings.envFile,
        profile: false, sidecarsEnabled: true, cutSilenceEncoderPreset: settings.cutSilenceEncoderPreset,
        silencePreviewHeight: settings.silencePreviewHeight, silencePreviewFps: settings.silencePreviewFps,
      });
      if (started.project) { creatorProjectRef.current = started.project; setCreatorProject(started.project); }
      setOutputPath(started.outputPath ?? result.outputPath); setActiveRunId(started.runId);
    } catch (error) { setRunState("failed"); throw error; }
  }

  async function startRun() {
    if (!settings || !configPaths || !coreSettings) return;
    downloadCancelled.current = false;
    try {
      let runInputPath = inputPath;
      let runAnalysis = analysis;
      let runMomentSpec = momentSpec;
      let runOutputPath = outputPath;
      let runSidecarDir = sidecarDir;
      if (downloadRequested) {
        clearLogs(); setRunState("running"); setElapsedMs(0); setActiveRunId("");
        setDownloading(true); setAcquiringSource(true); setDownloadProgress(null); downloadCancelled.current = false;
        try {
          await projectSaveQueue.current;
          let downloadProject = creatorProjectRef.current;
          if (!downloadProject) {
            downloadProject = await window.subtitler.createProject(t("input.downloadProject"));
            creatorProjectRef.current = downloadProject; setCreatorProject(downloadProject);
          }
          const downloadDirectory = pendingDownload.directory || joinPath(downloadProject.directory, "Sources");
          if (downloadCancelled.current) { setRunState("cancelled"); return; }
          if (extraction) {
            if (!momentPanel.current) throw new Error(t("moments.ready"));
            runMomentSpec = await momentPanel.current.download(downloadDirectory);
            const downloadedAnalysis = await window.subtitler.analyzeMedia(runMomentSpec.sourcePath);
            const sources = buildEditorialSources([{ path: runMomentSpec.sourcePath, analysis: downloadedAnalysis }]);
            const saved = await window.subtitler.updateProject({ ...downloadProject, recordings: [...downloadProject.recordings, ...sources.map(source => ({ id: crypto.randomUUID(), source }))] });
            creatorProjectRef.current = saved; setCreatorProject(saved);
          } else {
            const acquired = await window.subtitler.acquireSource(subtitleDownload.url.trim(), undefined, downloadDirectory);
            runInputPath = acquired.path;
            runAnalysis = await window.subtitler.analyzeMedia(runInputPath);
            handleInput(runInputPath);
            setSubtitleDownload(value => ({ ...value, url: "" }));
            runOutputPath = defaultOutputPath(runInputPath, workflow);
            runSidecarDir = defaultSidecarDir(runInputPath);
          }
          if (downloadCancelled.current) { setRunState("cancelled"); return; }
        } finally { setDownloading(false); setAcquiringSource(false); }
      }
      if (extraction && runMomentSpec) {
        await persistWorkflowSettings(false);
        clearLogs(); setRunState("running"); setElapsedMs(0); setMomentOutput("");
        const output = runMomentSpec.sourcePath.replace(/\.[^\\/.]+$/, "") + `.moments-${Date.now()}.json`;
        const result = await window.subtitler.startRun({
          workflow: "hosted-long-stream", moments: runMomentSpec, inputPath: runMomentSpec.sourcePath, outputPath: output,
          configPath: configPaths["hosted-long-stream"], envFile: settings.envFile, audioTrack: downloadRequested ? 0 : momentAudioTrack,
          profile: true, sidecarsEnabled: true, cutSilenceEncoderPreset: settings.cutSilenceEncoderPreset,
          silencePreviewHeight: settings.silencePreviewHeight, silencePreviewFps: settings.silencePreviewFps,
        });
        setMomentOutput(output); setActiveRunId(result.runId);
        return;
      }
      if (reviewedEditorialProject) {
        clearLogs();
        setRunState("running");
        setElapsedMs(0);
        setEditorialCutApplication(null);
        const result = await window.subtitler.applyReviewedEditorialCuts(reviewedEditorialProject);
        setEditorialCutApplication(result);
        return;
      }
      await persistWorkflowSettings(false);
      await projectSaveQueue.current;
      let project = creatorProjectRef.current;
      if (!editorialResumeCheckpoint && !editorialExtensionCheckpoint) {
        const sources = editorialMapEnabled ? editorialProject.sources : buildEditorialSources([{ path: runInputPath, analysis: runAnalysis ?? await window.subtitler.analyzeMedia(runInputPath) }]);
        if (!project) project = await window.subtitler.createProject((runInputPath.split(/[\\/]/).pop() ?? "Untitled project").replace(/\.[^.]+$/, ""));
        const recordings = editorialMapEnabled
          ? sources.map((source) => ({ id: project!.recordings.find((recording) => recording.source.visualPath === source.visualPath)?.id ?? crypto.randomUUID(), source }))
          : project.recordings.some((recording) => speechPath(recording.source) === runInputPath)
            ? project.recordings : [...project.recordings, ...sources.map((source) => ({ id: crypto.randomUUID(), source }))];
        project = await window.subtitler.updateProject({ ...project, recordings, editorial: editorialMapEnabled ? editorialProject : project.editorial });
        creatorProjectRef.current = project; setCreatorProject(project);
      }
      clearLogs();
      setRunState("running");
      setElapsedMs(0);
      const result = await window.subtitler.startRun({
        creatorProjectDirectory: !editorialResumeCheckpoint && !editorialExtensionCheckpoint ? project?.directory : undefined,
        creatorRecordingId: !editorialMapEnabled ? project?.recordings.find((recording) => speechPath(recording.source) === runInputPath)?.id : undefined,
        workflow,
        freshRun: false,
        inputPath: editorialResumeCheckpoint || editorialExtensionCheckpoint || runInputPath,
        outputPath: runOutputPath,
        configPath: configPaths[workflow],
        envFile: settings.envFile,
        audioTrack: downloadRequested ? 0 : coreSettings.audioTrack,
        sidecarDir: settings.sidecarsEnabled ? runSidecarDir : undefined,
        sidecarsEnabled: settings.sidecarsEnabled,
        profile: coreSettings.diagnostics.profile,
        cutSilenceEncoderPreset: settings.cutSilenceEncoderPreset,
        silencePreviewHeight: settings.silencePreviewHeight,
        silencePreviewFps: settings.silencePreviewFps,
        editorialProject: editorialMapEnabled && (!editorialResumeCheckpoint || Boolean(editorialExtensionCheckpoint)) ? {
          ...editorialProject,
          outputLocale: editorialExtensionCheckpoint ? editorialProject.outputLocale ?? "en" : settings.appLocale
        } : undefined,
        editorialCheckpoint: editorialMapEnabled ? (editorialResumeCheckpoint || editorialExtensionCheckpoint || undefined) : undefined,
        editorialCheckpointSources: editorialMapEnabled && (editorialResumeCheckpoint || editorialExtensionCheckpoint) && editorialProject.sources.length ? editorialProject.sources : undefined,
        editorialRestartFrom: editorialMapEnabled && (editorialResumeCheckpoint || editorialExtensionCheckpoint) ? editorialRestartFrom : undefined,
        editorialExtend: editorialMapEnabled && Boolean(editorialExtensionCheckpoint) ? true : undefined
      });
      if (result.project) { creatorProjectRef.current = result.project; setCreatorProject(result.project); }
      if (result.outputPath) setOutputPath(result.outputPath);
      if (result.sidecarDir) setSidecarDir(result.sidecarDir);
      setActiveRunId(result.runId);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setRunState(downloadCancelled.current ? "cancelled" : "failed");
      setActiveRunId("");
      if (!reviewedEditorialProject) replaceLogs(message ? `${message}\n` : "");
      setNotice(message || t("notice.runStartFailed"));
    }
  }

  async function cancelRun(immediate = false, requestedRunId = activeRunId) {
    if (downloading) { downloadCancelled.current = true; await window.subtitler.cancelSourceAcquisition(); return; }
    if (!requestedRunId) return;
    await window.subtitler.cancelRun(requestedRunId, immediate);
  }

  function handleRunEvent(event: RunEvent) {
    if (event.type === "started") {
      setActiveRunId(event.runId);
      setStartedAt(Date.parse(event.startedAt));
      replaceLogs(`$ ${event.commandPreview}\n`);
    } else if (event.type === "stdout" || event.type === "stderr") {
      appendLog(event.text);
    } else if (event.type === "exit") {
      const directory = creatorProjectRef.current?.directory;
      if (directory) void window.subtitler.openProject(directory).then((project) => { creatorProjectRef.current = project; setCreatorProject(project); }).catch((error: unknown) => setNotice(String(error)));
      setElapsedMs(event.elapsedMs);
      setRunState(event.cancelled ? "cancelled" : event.code === 0 ? "succeeded" : "failed");
      if (!event.cancelled && event.code === 0) appendLog("\nRun complete.\n");
      setActiveRunId("");
      setSilenceReview(null);
      setBrollReview(null);
    } else if (event.type === "error") {
      setRunState("failed");
      appendLog(`\n${event.message}\n`);
    } else if (event.type === "silence-review-required") {
      setRunState("reviewing");
      setSilenceReview({ runId: event.runId, reviewId: event.reviewId, candidates: event.candidates });
    } else if (event.type === "broll-review-required") {
      setRunState("reviewing");
      setBrollReview({ runId: event.runId, reviewId: event.reviewId, candidates: event.candidates });
    } else if (event.type === "silence-cut-output") {
      appendLog(`\nCut video: ${event.path}\n`);
      setNotice(t("notice.cutVideoCreated"));
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
    catch (error) { setNotice(t("notice.encoderCheckFailed", { error: error instanceof Error ? error.message : String(error) })); }
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

  async function submitBrollReview(decisions: BrollReviewDecision[]) {
    if (!brollReview) return;
    await window.subtitler.submitBrollReview(brollReview.runId, brollReview.reviewId, decisions);
    setBrollReview(null);
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
    return <div className="loading" role="alert"><p>{t("shell.startupError")}</p><p>{startupError}</p><button onClick={() => void loadInitialState()}>{t("shell.tryAgain")}</button><button onClick={async () => { await window.subtitler.resetAppState(); await loadInitialState(); }}>{t("shell.resetSettings")}</button></div>;
  }
  if (!settings || !configs || !configPaths || !coreSettings) {
    return <div className="loading">{t("shell.loading")}</div>;
  }

  if (silenceReview) return <SilenceReviewScreen runId={silenceReview.runId} reviewId={silenceReview.reviewId} candidates={silenceReview.candidates} onSubmit={submitSilenceReview} onCancel={() => cancelRun(true, silenceReview.runId)} />;
  if (brollReview) return <BrollReviewScreen key={brollReview.reviewId} runId={brollReview.runId} reviewId={brollReview.reviewId} candidates={brollReview.candidates} onSubmit={submitBrollReview} onCancel={() => cancelRun(true, brollReview.runId)} />;

  const currentWorkflowFamily = workflowFamily(workflow);
  const currentSettingsExpansion = settingsExpansion[currentWorkflowFamily] ?? defaultSettingsExpansion({
    pythonReady,
    ffmpegReady: Boolean(runtimeStatus?.ffmpeg.ready),
    ytDlpReady: Boolean(runtimeStatus?.ytDlp.ready),
    alignmentInstalled: Boolean(runtimeStatus?.alignment.installed),
    envExists: envStatus.exists,
    serverExists: Boolean(pathStatus.llamaServer?.exists),
  });

  return (
    <main className={view === "main" ? (extraction ? "app creator-app moments-app" : "app creator-app") : "app"}>
      <header className="topbar">
        <div>
          <h1>SubUtl</h1>
          <div className="subtle" title={projectRoot}>{creatorProject?.name ?? t("project.workspace")}</div>
        </div>
        <div className="topbar-controls">
          <ModeSelector workflow={workflow} extraction={extraction} onExtract={() => { setExtraction(true); setView((current) => current === "library" ? current : "main"); }} onChange={(next) => { setExtraction(false); setView((current) => current === "library" ? current : "main"); setWorkflow(next === "hosted-long-stream" ? next : lastSubtitleWorkflow.current); }} disabled={runState === "running" || runState === "reviewing" || acquiringSource || projectSaving} />
          <ThemeSelector value={settings.theme} onChange={(theme) => {
            const next = { ...settings, theme };
            setSettings(next);
            void saveSettings(next);
          }} />
          {view === "main" ? (
            <>
              <button className="topbar-button" onClick={() => { setLibraryVisited(true); setView("library"); }}><Library size={16} /> {t("shell.library")}</button>
              <button className="topbar-button" onClick={() => setView("settings")}><SettingsIcon size={16} /> {t("shell.settings")}</button>
            </>
          ) : (
            <button className="topbar-button" onClick={() => setView("main")}><ArrowLeft size={16} /> {t("common.back")}</button>
          )}
        </div>
      {ytDlpOutdated && <section className="row" style={{ gridColumn: "1 / -1" }} role="status"><span>{t("moments.outdated")}</span><button disabled={ytDlpUpdating || Boolean(runtimeAction) || acquiringSource || runState === "running"} onClick={() => { setYtDlpUpdating(true); void window.subtitler.installOrUpdateYtDlp().then(() => { setYtDlpOutdated(false); void refreshRuntimeStatus(); }).catch((error: unknown) => setNotice(String(error))).finally(() => setYtDlpUpdating(false)); }}>{t("moments.update")}</button></section>}
      </header>
      {view === "main" && !extraction && <CreatorWorkspace suggestedName={inputPath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "")} onResume={resumeProjectResult} onBusyChange={setAcquiringSource} project={creatorProject} selectedRecording={creatorRecordingId} disabled={runState === "running" || runState === "reviewing" || acquiringSource || projectSaving} onProject={receiveProject} onSelect={(id, source) => { setCreatorRecordingId(id); handleInput(editorialMapEnabled ? source.visualPath : speechPath(source)); }} />}

      {libraryVisited && <div className="library-host" hidden={view !== "library"}><MediaLibraryScreen /></div>}
      {view === "library" ? null : view === "settings" ? (
        <div className="settings-view">
          <SettingsPanel
            momentAnalysisModel={extraction ? String(configs?.["hosted-long-stream"].editorial?.analysis_model ?? "gpt-5.6-luna") : undefined}
            workflow={workflow}
            appLocale={settings.appLocale}
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
            ytDlpDenoPath={settings.ytDlpDenoPath ?? ""}
            ytDlpCookiesBrowser={settings.ytDlpCookiesBrowser ?? ""}
            ytDlpCookiesProfile={settings.ytDlpCookiesProfile ?? ""}
            sidecarsEnabled={settings.sidecarsEnabled}
            sidecarDir={sidecarDir}
            outputPath={outputPath}
            runActive={runState === "running"}
            expansion={currentSettingsExpansion}
            onToggleExpansion={(section) => setSettingsExpansion((current) => ({
              ...current,
              [currentWorkflowFamily]: updateSettingsExpansion(current[currentWorkflowFamily] ?? currentSettingsExpansion, section),
            }))}
            onAppLocale={(appLocale) => {
              const next = { ...settings, appLocale };
              setSettings(next);
              void saveSettings(next, false);
            }}
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
            onInstallOrUpdateYtDlp={installOrUpdateYtDlp}
            onDeleteYtDlp={deleteManagedYtDlp}
            onYtDlpDenoPath={(ytDlpDenoPath) => {
              const next = { ...settings, ytDlpDenoPath };
              setSettings(next);
              void saveSettings(next);
            }}
            onYtDlpCookiesBrowser={(ytDlpCookiesBrowser) => {
              const next = { ...settings, ytDlpCookiesBrowser };
              setSettings(next);
              void saveSettings(next);
            }}
            onYtDlpCookiesProfile={(ytDlpCookiesProfile) => {
              const next = { ...settings, ytDlpCookiesProfile };
              setSettings(next);
              void saveSettings(next);
            }}
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
            {extraction ? <MomentExtractionPanel ref={momentPanel} download={momentDownload} onDownload={setMomentDownload} defaultDownloadLocation={projectDownloadLocation} initial={momentDraft.current} onDraft={onMomentDraft} disabled={runState === "running"} onBusy={setAcquiringSource} onReady={onMomentReady} /> : editorialMapEnabled ? <SilenceProjectPanel value={editorialProject} disabled={runState === "running"}
              onChange={changeEditorialProject} onPrimarySource={handleInput} /> : <InputPanel
              download={subtitleDownload} onDownload={setSubtitleDownload} defaultDownloadLocation={projectDownloadLocation}
              inputPath={inputPath}
              audioTrack={coreSettings.audioTrack}
              analysis={analysis}
              analyzing={analyzing}
              analysisError={analysisError}
              disabled={runState === "running"}
              onInput={(path) => { setSubtitleDownload(value => ({ ...value, url: "" })); handleInput(path); }}
              onAudioTrack={(value) => setCoreSettings({ ...coreSettings, audioTrack: value })}
            />}

          </div>
          <div className="resize-divider column-divider" role="separator" aria-label={t("shell.resizeColumns")} aria-orientation="vertical" aria-valuemin={38} aria-valuemax={72} aria-valuenow={Math.round(inputWidth)} aria-valuetext={t("shell.inputWidth", { percent: Math.round(inputWidth) })} tabIndex={0} title={t("shell.resizeColumnsHelp")} onPointerDown={startColumnResize} onKeyDown={(event) => resizeWithKeyboard(event, inputWidth, "vertical", 38, 72, setInputWidth)} />
          <div className="flow-side">


          {extraction ? <section className="panel stack"><div className="panel-title">{t("project.results")}</div><p>{t("moments.outputHelp")}</p>
            {momentOutput && runState === "succeeded" && <><button onClick={() => void window.subtitler.openPath(momentOutput.replace(/\.json$/, ".html"))}>{t("moments.report")}</button><button onClick={() => void window.subtitler.openPath(momentOutput.replace(/\.json$/, ".exo"))}>{t("moments.exo")}</button><button onClick={() => void window.subtitler.showItemInFolder(momentOutput)}>{t("project.files")}</button></>}
          </section> : (editorialResumeCheckpoint || editorialExtensionCheckpoint || reviewedEditorialProject) ? <OutputPanel
            outputPath={outputPath}
            editorial={editorialMapEnabled}
            disabled={runState === "running" || Boolean(editorialResumeCheckpoint || editorialExtensionCheckpoint)}
            onOutput={setOutputPath}
          /> : <section className="panel creator-output-summary"><div className="panel-title">{t("project.results")}</div>
            {creatorProject?.results.some((result) => result.status === "complete") ? creatorProject.results.filter((result) => result.status === "complete").slice(-3).reverse().flatMap((result) => (result.deliverablePaths ?? [result.deliverablePath ?? result.outputPath]).map((file) => <div className="result-file-row" key={`${result.id}:${file}`}>
              <span>{file.split(/[\\/]/).pop()}</span>
              <button onClick={() => void window.subtitler.showItemInFolder(file)}>{t("project.files")}</button>
            </div>)) : <p>{t("project.emptyResults")}</p>}
          </section>}
           {!extraction && <AdditionalSettingsPanel audioTracks={analysis?.audioTracks} paired={editorialProject.sources.length > 0 && editorialProject.sources.every((source) => source.mode === "paired" && source.roleConfirmed)} workflow={workflow} settings={coreSettings} encoder={settings.cutSilenceEncoderPreset} encoderReady={Boolean(selectedEncoderProbe?.available) && !probingEncoders} encoderChecking={probingEncoders} hasVideo={Boolean(analysis?.videoCodec)} frameRateMode={analysis?.frameRateMode ?? "unknown"} disabled={runState === "running"} onConfigure={openCutSilenceSettings} onChange={setCoreSettings} />}
          {!extraction && !editorialMapEnabled && <GlossaryPanel value={glossary} onChange={setGlossary} onSave={saveGlossary} onImport={importGlossary} />}
          </div>
        </div>
        <div className="resize-divider log-divider" role="separator" aria-label={t("shell.resizeLogs")} aria-orientation="horizontal" aria-valuemin={14} aria-valuemax={48} aria-valuenow={Math.round(logsHeight)} aria-valuetext={t("shell.logHeight", { percent: Math.round(logsHeight) })} tabIndex={0} title={t("shell.resizeLogsHelp")} onPointerDown={startLogResize} onKeyDown={(event) => resizeWithKeyboard(event, logsHeight, "horizontal", 14, 48, setLogsHeight)} />
        <div className="logs-row">
          <RunPanel processingMode={!extraction && !editorialMapEnabled ? workflow as "local" | "hosted" : undefined} onProcessingMode={setWorkflow} download={downloadRequested || downloading} downloadProgress={downloading ? downloadProgress : undefined} blockedReason={runBlockedReason} onConfigure={() => setView("settings")} state={runState} elapsed={elapsed} canRun={canRun} onRun={startRun} onCancel={cancelRun}
               />
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
