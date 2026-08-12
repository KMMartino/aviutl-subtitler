import { ArrowDown, ArrowUp, ChevronDown, FilePlus2, LoaderCircle, Plus, RotateCcw, Search, Trash2, X } from "lucide-react";
import { useEffect, useState, type DragEvent } from "react";
import { buildEditorialSources, setPairedAudioRole, type EditorialMediaCandidate } from "../lib/editorialPairing";
import type { EditorialCheckpointInspection, EditorialCheckpointSummary, EditorialGameSummary, EditorialProjectRequest, EditorialRestartMode, EditorialSourceSelection, MediaAnalysis } from "../lib/types";
import { useI18n } from "../i18n";
import type { TranslationKey, TranslationParameters } from "../../shared/i18n";

type T = (key: TranslationKey, parameters?: TranslationParameters) => string;

type Props = {
  value: EditorialProjectRequest;
  disabled?: boolean;
  resumeCheckpoint: string;
  resumeRestartFrom: EditorialRestartMode;
  extensionCheckpoint: string;
  extensionBaseCount: number;
  onChange(value: EditorialProjectRequest): void;
  onRecoverProject(value: EditorialProjectRequest): void;
  onPrimarySource(path: string): void;
  onResumeCheckpoint(path: string, restartFrom: EditorialRestartMode): void;
  onBeginExtension(path: string, analyzedSourceCount: number): void;
  onCancelExtension(path: string): void;
  onDeclineReuse(path: string): void;
};

export default function EditorialProjectPanel({ value, disabled = false, resumeCheckpoint, resumeRestartFrom, extensionCheckpoint, extensionBaseCount, onChange, onRecoverProject, onPrimarySource, onResumeCheckpoint, onBeginExtension, onCancelExtension, onDeclineReuse }: Props) {
  const { locale, t } = useI18n();
  const [inspecting, setInspecting] = useState(false);
  const [error, setError] = useState("");
  const [managedCheckpoints, setManagedCheckpoints] = useState<EditorialCheckpointSummary[]>([]);
  const [checkpointPickerOpen, setCheckpointPickerOpen] = useState(false);
  const [checkpointInspections, setCheckpointInspections] = useState<Record<string, EditorialCheckpointInspection>>({});
  const [checkpointRestartModes, setCheckpointRestartModes] = useState<Record<string, EditorialRestartMode>>({});
  const [suggestedCheckpoint, setSuggestedCheckpoint] = useState("");
  const [games, setGames] = useState<EditorialGameSummary[]>([]);
  const [gamePickerOpen, setGamePickerOpen] = useState(false);
  const [gameSearch, setGameSearch] = useState("");
  const totalSeconds = value.sources.reduce((total, source) => total + source.durationSeconds, 0);
  const sliderMaximum = Math.max(60, Math.round(totalSeconds));
  const rangeSpan = Math.max(1, sliderMaximum - 60);
  const minimumPercent = Math.max(0, Math.min(100, ((value.targetDurationMinSeconds - 60) / rangeSpan) * 100));
  const maximumPercent = Math.max(minimumPercent, Math.min(100, ((value.targetDurationMaxSeconds - 60) / rangeSpan) * 100));

  useEffect(() => {
    let active = true;
    void window.subtitler.listEditorialCheckpoints().then((items) => {
      if (active) setManagedCheckpoints(items);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    void window.subtitler.listEditorialGames().then((items) => {
      if (active) setGames(items);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  async function selectGame(title: string) {
    const normalized = title.trim();
    if (!normalized) return;
    onChange({ ...value, titleOrGame: normalized });
    setGamePickerOpen(false);
    setGameSearch("");
    try {
      const remembered = await window.subtitler.rememberEditorialGame(normalized);
      setGames((items) => [remembered, ...items.filter((item) => item.title.toLocaleLowerCase() !== remembered.title.toLocaleLowerCase())]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function chooseSources() {
    if (disabled || inspecting) return;
    const paths = await window.subtitler.chooseInputFiles(value.sources[0]?.visualPath);
    if (paths?.length) await addSources(paths);
  }

  async function openCheckpointPicker() {
    if (disabled) return;
    setCheckpointPickerOpen(true);
    setInspecting(true);
    setError("");
    try {
      const checkpoints = await window.subtitler.listEditorialCheckpoints();
      setManagedCheckpoints(checkpoints);
      await inspectCheckpointList(checkpoints);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setInspecting(false);
    }
  }

  async function inspectCheckpointList(
    checkpoints: EditorialCheckpointSummary[],
    selectedSources: EditorialSourceSelection[] | undefined = value.sources.length ? value.sources : undefined,
  ) {
    for (let start = 0; start < checkpoints.length; start += 4) {
      await Promise.all(
        checkpoints
          .slice(start, start + 4)
          .map((checkpoint) => inspectCheckpoint(checkpoint.path, false, selectedSources)),
      );
    }
  }

  async function browseCheckpoint() {
    const path = await window.subtitler.chooseFile();
    if (!path) return;
    const inspection = await inspectCheckpoint(path, true);
    if (!inspection) return;
    const checkpoints = await window.subtitler.listEditorialCheckpoints();
    setManagedCheckpoints(checkpoints.length ? checkpoints : [summaryFromInspection(path, inspection)]);
  }

  async function removeManagedCheckpoint(checkpoint: string) {
    await window.subtitler.removeEditorialCheckpoint(checkpoint);
    setManagedCheckpoints((items) => items.filter((item) => item.path !== checkpoint));
  }

  async function inspectCheckpoint(
    path: string,
    explicit: boolean,
    selectedSources: EditorialSourceSelection[] | undefined = value.sources.length ? value.sources : undefined,
  ): Promise<EditorialCheckpointInspection | null> {
    try {
      const inspection = await window.subtitler.inspectEditorialCheckpoint(path, selectedSources);
      if (!inspection.matches_sources) {
        if (explicit) setError(inspection.source_error || t("editorial.filesMismatch"));
        return null;
      }
      setCheckpointInspections((items) => ({ ...items, [path]: inspection }));
      setCheckpointRestartModes((items) => ({ ...items, [path]: items[path] ?? inspection.recommended_restart_from }));
      return inspection;
    } catch (caught) {
      if (explicit) setError(caught instanceof Error ? caught.message : String(caught));
      return null;
    }
  }

  function loadCheckpoint(path: string, inspection: EditorialCheckpointInspection, addFollowups: boolean) {
    const restartFrom = checkpointRestartModes[path] ?? inspection.recommended_restart_from;
    if (addFollowups) {
      const matched = new Set(inspection.matched_selected_indices);
      onRecoverProject({ ...inspection.project_request, sources: [...inspection.project_request.sources, ...value.sources.filter((_, index) => !matched.has(index))] });
      onBeginExtension(path, inspection.project_request.sources.length);
    } else {
      onRecoverProject(inspection.project_request);
      onResumeCheckpoint(path, restartFrom);
    }
    setCheckpointPickerOpen(false);
    setSuggestedCheckpoint("");
  }

  async function addSources(paths: string[]) {
    const existingCandidates = value.sources.flatMap(sourceCandidates);
    const existing = new Set(existingCandidates.map((candidate) => candidate.path.toLocaleLowerCase()));
    const additions: EditorialMediaCandidate[] = [];
    setInspecting(true);
    setError("");
    try {
      for (const path of paths) {
        if (!path || existing.has(path.toLocaleLowerCase())) continue;
        const analysis = await window.subtitler.analyzeMedia(path);
        if (!analysis.videoCodec || analysis.durationSeconds === null || analysis.durationSeconds <= 0) {
          throw new Error(t("editorial.badVideo", { path }));
        }
        additions.push({ path, analysis });
        existing.add(path.toLocaleLowerCase());
      }
      if (!additions.length) return;
      let sources = buildEditorialSources([...existingCandidates, ...additions]);
      for (const previous of value.sources) {
        if (previous.mode !== "paired" || !previous.roleConfirmed) continue;
        const key = pairKey(previous.audioPath, previous.visualPath);
        sources = sources.map((source) => source.mode === "paired" && pairKey(source.audioPath, source.visualPath) === key
          ? setPairedAudioRole(source, previous.audioPath)
          : source);
      }
      const nextTotal = sources.reduce((total, source) => total + source.durationSeconds, 0);
      const upper = Math.min(nextTotal, 12 * 3600);
      const lower = Math.min(upper, Math.max(60, upper * 0.75));
      onChange({
        ...value,
        sources,
        targetDurationMinSeconds: value.sources.length ? Math.min(value.targetDurationMinSeconds, upper) : lower,
        targetDurationMaxSeconds: value.sources.length ? Math.min(value.targetDurationMaxSeconds, upper) : upper
      });
      onPrimarySource(sources[0].visualPath);
      const reusable = extensionCheckpoint ? null : await window.subtitler.findEditorialCheckpoint(sources);
      if (reusable) {
        const checkpoints = await window.subtitler.listEditorialCheckpoints();
        setManagedCheckpoints(checkpoints.length ? checkpoints : [summaryFromInspection(reusable.path, reusable.inspection)]);
        setCheckpointInspections((items) => ({ ...items, [reusable.path]: reusable.inspection }));
        setCheckpointRestartModes((items) => ({ ...items, [reusable.path]: reusable.inspection.recommended_restart_from }));
        setSuggestedCheckpoint(reusable.path);
        setCheckpointPickerOpen(true);
        await inspectCheckpointList(
          checkpoints.filter((checkpoint) => checkpoint.path !== reusable.path),
          sources,
        );
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setInspecting(false);
    }
  }

  function drop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    if (disabled || inspecting) return;
    const paths = Array.from(event.dataTransfer.files).map((file) => window.subtitler.filePath(file)).filter(Boolean);
    void addSources(paths);
  }

  function move(index: number, offset: -1 | 1) {
    const target = index + offset;
    if (target < 0 || target >= value.sources.length) return;
    const sources = [...value.sources];
    [sources[index], sources[target]] = [sources[target], sources[index]];
    onChange({ ...value, sources });
    onPrimarySource(sources[0].visualPath);
  }

  function remove(index: number) {
    const sources = value.sources.filter((_, sourceIndex) => sourceIndex !== index);
    const nextTotal = sources.reduce((total, source) => total + source.durationSeconds, 0);
    onChange({
      ...value,
      sources,
      targetDurationMinSeconds: Math.min(value.targetDurationMinSeconds, nextTotal || 60),
      targetDurationMaxSeconds: Math.min(value.targetDurationMaxSeconds, nextTotal || 60)
    });
    onPrimarySource(sources[0]?.visualPath ?? "");
  }

  function confirmAudioRole(index: number, audioPath: string) {
    const sources = value.sources.map((source, sourceIndex) => sourceIndex === index
      ? setPairedAudioRole(source, audioPath)
      : source);
    onChange({ ...value, sources });
    onPrimarySource(sources[0]?.visualPath ?? "");
  }

  const updateMinimum = (seconds: number) => onChange({
    ...value,
    targetDurationMinSeconds: Math.min(seconds, value.targetDurationMaxSeconds)
  });
  const updateMaximum = (seconds: number) => onChange({
    ...value,
    targetDurationMaxSeconds: Math.max(seconds, value.targetDurationMinSeconds)
  });

  return <section className="panel editorial-project-panel" onDragOver={(event) => event.preventDefault()} onDrop={drop}>
    <div className="panel-title editorial-panel-title"><span>{t("editorial.title")}</span><button type="button" disabled={disabled || inspecting} onClick={() => void openCheckpointPicker()}><RotateCcw size={16} /> {t("editorial.openCheckpoint")}</button></div>
    {!value.sources.length && !resumeCheckpoint && <div className="editorial-drop-zone">
      <FilePlus2 size={28} />
      <strong>{t("editorial.dropTitle")}</strong>
      <span>{t("editorial.dropDetail")}</span>
      <button disabled={disabled || inspecting} onClick={() => void chooseSources()}>{inspecting ? <LoaderCircle className="spin" size={16} /> : <FilePlus2 size={16} />} {t("editorial.chooseVideos")}</button>
    </div>}
    {value.sources.length > 0 && <>
      <div className="editorial-source-header">
        <strong>{t("editorial.sourcesOrder")}</strong>
        {resumeCheckpoint
          ? <button disabled={disabled} onClick={() => onBeginExtension(resumeCheckpoint, value.sources.length)}><FilePlus2 size={16} /> {t("editorial.addFollowups")}</button>
          : <button disabled={disabled || inspecting} onClick={() => void chooseSources()}>{inspecting ? <LoaderCircle className="spin" size={16} /> : <FilePlus2 size={16} />} {extensionCheckpoint ? t("editorial.addFollowupVideos") : t("editorial.addVideos")}</button>}
      </div>
      <div className="editorial-source-list">
        {value.sources.map((source, index) => <div className={`editorial-source-row${source.mode === "paired" ? " paired" : ""}`} key={source.mode === "paired" ? pairKey(source.audioPath, source.visualPath) : source.path}>
          <span className="editorial-source-order">{index + 1}</span>
          <span title={source.mode === "paired" ? `${source.audioPath}\n${source.visualPath}` : source.path}>
            <strong>{source.mode === "paired" ? `${fileName(source.visualPath)} + ${fileName(source.audioPath)}` : fileName(source.path)}</strong>
            {source.mode === "paired"
              ? <><small>{t("editorial.visualRole", { name: fileName(source.visualPath) })}</small><small>{t("editorial.audioRole", { name: fileName(source.audioPath) })}</small><small>{t("editorial.pairedBy", { basis: source.pairingBasis, duration: formatDuration(source.durationSeconds, t) })}</small></>
              : <small>{t("editorial.singleAnalysis", { duration: formatDuration(source.durationSeconds, t) })}</small>}
            {source.mode === "paired" && !source.roleConfirmed && <label className="editorial-role-prompt">
              <span>{t("editorial.facecamQuestion")}</span>
              <select value="" onChange={(event) => confirmAudioRole(index, event.target.value)}>
                <option value="" disabled>{t("editorial.selectFacecam")}</option>
                <option value={source.audioPath}>{fileName(source.audioPath)}</option>
                <option value={source.visualPath}>{fileName(source.visualPath)}</option>
              </select>
            </label>}
            {source.mode === "paired" && source.roleConfirmed && <button className="editorial-swap-role" type="button" disabled={disabled || Boolean(resumeCheckpoint)} onClick={() => confirmAudioRole(index, source.visualPath)}>{t("editorial.swapRoles")}</button>}
          </span>
          <button className="icon-button" aria-label={t("editorial.moveEarlier")} disabled={disabled || Boolean(resumeCheckpoint) || index < extensionBaseCount || index === 0 || index - 1 < extensionBaseCount} onClick={() => move(index, -1)}><ArrowUp size={15} /></button>
          <button className="icon-button" aria-label={t("editorial.moveLater")} disabled={disabled || Boolean(resumeCheckpoint) || index < extensionBaseCount || index === value.sources.length - 1} onClick={() => move(index, 1)}><ArrowDown size={15} /></button>
          <button className="icon-button" aria-label={t("editorial.removeSource")} disabled={disabled || Boolean(resumeCheckpoint) || index < extensionBaseCount} onClick={() => remove(index)}><Trash2 size={15} /></button>
        </div>)}
      </div>
    </>}
    {resumeCheckpoint && <div className="editorial-resume-row"><span title={resumeCheckpoint}><strong>{t("editorial.usingPrior")}</strong><small>{fileName(resumeCheckpoint)} · {restartLabel(resumeRestartFrom, t)}</small></span><button className="icon-button" aria-label={t("editorial.newInstead")} disabled={disabled} onClick={() => onResumeCheckpoint("", "compatible")}><X size={16} /></button></div>}
    {extensionCheckpoint && <div className="editorial-resume-row"><span title={extensionCheckpoint}><strong>{t("editorial.addingPrior")}</strong><small>{t("editorial.preservedSources", { count: extensionBaseCount, noun: locale === "ja" ? "件が" : extensionBaseCount === 1 ? "source is" : "sources are" })}</small></span><button className="icon-button" aria-label={t("editorial.cancelFollowups")} disabled={disabled} onClick={() => onCancelExtension(extensionCheckpoint)}><X size={16} /></button></div>}
    <fieldset disabled={disabled || Boolean(resumeCheckpoint)} className="editorial-new-project-fields">
    <div className="editorial-project-fields">
      <label><span className="field-label">{t("editorial.titleOrGame")}</span><div className="editorial-game-picker">
        <button type="button" className="editorial-game-picker-button" disabled={disabled} aria-expanded={gamePickerOpen} onClick={() => setGamePickerOpen((open) => !open)}><span>{value.titleOrGame || t("editorial.selectGame")}</span><ChevronDown size={16} /></button>
        {gamePickerOpen && <div className="editorial-game-menu">
          <div className="editorial-game-options">{games.filter((game) => game.title.toLocaleLowerCase().includes(gameSearch.trim().toLocaleLowerCase())).map((game) => <button type="button" key={game.title.toLocaleLowerCase()} onClick={() => void selectGame(game.title)}><span>{game.title}</span>{game.revision > 0 && <small>{t("editorial.learnedProfile", { revision: game.revision })}</small>}</button>)}{!games.length && <span className="muted">{t("editorial.noGames")}</span>}</div>
          <div className="editorial-game-search"><Search size={15} /><input autoFocus value={gameSearch} placeholder={t("editorial.searchGames")} onChange={(event) => setGameSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void selectGame(gameSearch); }} />{gameSearch.trim() && !games.some((game) => game.title.toLocaleLowerCase() === gameSearch.trim().toLocaleLowerCase()) && <button type="button" title={t("editorial.addGame", { name: gameSearch.trim() })} onClick={() => void selectGame(gameSearch)}><Plus size={15} /> {t("common.add")}</button>}</div>
        </div>}
      </div></label>
      <label><span className="field-label">{t("editorial.objective")}</span><textarea disabled={disabled} rows={3} value={value.objective} onChange={(event) => onChange({ ...value, objective: event.target.value })} /></label>
    </div>
    <div className="editorial-duration">
      <div><strong>{t("editorial.requestedDuration")}</strong><span>{formatDuration(value.targetDurationMinSeconds, t)}–{formatDuration(value.targetDurationMaxSeconds, t)}</span></div>
      <div className="dual-range" aria-label={t("editorial.durationRangeAria")}>
        <span className="dual-range-track"><span className="dual-range-selection" style={{ left: `${minimumPercent}%`, width: `${maximumPercent - minimumPercent}%` }} /></span>
        <input disabled={disabled || !value.sources.length} type="range" min={60} max={sliderMaximum} step={60} value={Math.min(value.targetDurationMinSeconds, sliderMaximum)} onChange={(event) => updateMinimum(Number(event.target.value))} aria-label={t("editorial.minimumDuration")} />
        <input disabled={disabled || !value.sources.length} type="range" min={60} max={sliderMaximum} step={60} value={Math.min(value.targetDurationMaxSeconds, sliderMaximum)} onChange={(event) => updateMaximum(Number(event.target.value))} aria-label={t("editorial.maximumDuration")} />
      </div>
      <small>{t("editorial.durationSummary", { count: value.sources.length, noun: locale === "ja" ? "件 " : value.sources.length === 1 ? "source" : "sources", duration: formatDuration(totalSeconds, t) })}</small>
    </div>
    <div className="editorial-project-fields optional">
      <label><span className="field-label">{t("editorial.mustKeep")}</span><textarea disabled={disabled} rows={3} value={value.mustKeepNotes.join("\n")} onChange={(event) => onChange({ ...value, mustKeepNotes: lines(event.target.value) })} /></label>
      <label><span className="field-label">{t("editorial.deemphasize")}</span><textarea disabled={disabled} rows={3} value={value.deEmphasizeNotes.join("\n")} onChange={(event) => onChange({ ...value, deEmphasizeNotes: lines(event.target.value) })} /></label>
    </div>
    {error && <div className="field-error" role="alert">{error}</div>}
    </fieldset>
    {checkpointPickerOpen && <div className="editorial-checkpoint-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setCheckpointPickerOpen(false); }}>
      <section className="editorial-checkpoint-modal" role="dialog" aria-modal="true" aria-label={t("editorial.checkpointDialog")}>
        <div className="editorial-checkpoint-modal-head"><div><strong>{t("editorial.checkpointDialog")}</strong><small>{t("editorial.checkpointNewest")}</small></div><button className="icon-button" aria-label={t("editorial.closeCheckpoint")} onClick={() => setCheckpointPickerOpen(false)}><X size={18} /></button></div>
        <div className="editorial-checkpoint-list">
          {!managedCheckpoints.length && <p className="muted">{t("editorial.noCheckpoints")}</p>}
          {managedCheckpoints.map((checkpoint) => {
            const inspection = checkpointInspections[checkpoint.path];
            const canAddFollowups = Boolean(inspection && value.sources.length && inspection.matched_selected_indices.length < value.sources.length);
            return <article className={`editorial-checkpoint-row${suggestedCheckpoint === checkpoint.path ? " suggested" : ""}`} key={checkpoint.path}>
              <div className="editorial-checkpoint-summary" title={checkpoint.path}><strong>{checkpoint.title}</strong><span>{checkpoint.objective}</span><small>{t("editorial.checkpointSummary", { count: checkpoint.sourceCount, noun: locale === "ja" ? "件 " : checkpoint.sourceCount === 1 ? "source" : "sources", status: checkpointStatusLabel(checkpoint.status, t), date: new Date(checkpoint.updatedAtUtc).toLocaleString(locale) })}</small></div>
              <label><span>{t("editorial.resumeFrom")}</span><select disabled={!inspection} value={checkpointRestartModes[checkpoint.path] ?? inspection?.recommended_restart_from ?? "compatible"} onChange={(event) => setCheckpointRestartModes((items) => ({ ...items, [checkpoint.path]: event.target.value as EditorialRestartMode }))}>{(inspection?.available_restart_from ?? ["compatible"]).map((mode) => <option key={mode} value={mode}>{restartLabel(mode, t)}</option>)}</select></label>
              <div className="button-row">{canAddFollowups && <button type="button" onClick={() => loadCheckpoint(checkpoint.path, inspection, true)}>{t("editorial.loadFollowups")}</button>}<button type="button" className="primary" disabled={!inspection} onClick={() => inspection && loadCheckpoint(checkpoint.path, inspection, false)}>{inspection ? t("editorial.openProject") : t("common.checking")}</button><button className="icon-button" aria-label={t("editorial.removeCheckpoint", { title: checkpoint.title })} disabled={disabled} onClick={() => void removeManagedCheckpoint(checkpoint.path)}><Trash2 size={15} /></button></div>
            </article>;
          })}
        </div>
        <div className="editorial-checkpoint-modal-actions"><button type="button" onClick={() => void browseCheckpoint()}>{t("editorial.browseCheckpoint")}</button>{suggestedCheckpoint && <button type="button" onClick={() => { onDeclineReuse(suggestedCheckpoint); setSuggestedCheckpoint(""); setCheckpointPickerOpen(false); }}>{t("editorial.startNewSelected")}</button>}</div>
      </section>
    </div>}
  </section>;
}

function lines(value: string): string[] {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

function fileName(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop() ?? path;
}

function formatDuration(seconds: number, t: T): string {
  const totalMinutes = Math.max(0, Math.round(seconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? t("duration.hoursMinutes", { hours, minutes: minutes.toString().padStart(2, "0") }) : t("duration.minutes", { minutes });
}

function pairKey(first: string, second: string): string {
  return [first.toLocaleLowerCase(), second.toLocaleLowerCase()].sort().join("\0");
}

function sourceCandidates(source: EditorialSourceSelection): EditorialMediaCandidate[] {
  if (source.mode === "paired") {
    return [
      { path: source.audioPath, analysis: storedAnalysis(source.audioDurationSeconds, source.audioWidth, source.audioHeight, source.audioFrameRate) },
      { path: source.visualPath, analysis: storedAnalysis(source.visualDurationSeconds, source.width, source.height, source.frameRate) },
    ];
  }
  return [{ path: source.path, analysis: storedAnalysis(source.durationSeconds, source.width, source.height, source.frameRate) }];
}

function storedAnalysis(durationSeconds: number, width: number | null, height: number | null, frameRate: number | null): MediaAnalysis {
  return {
    durationSeconds,
    formatName: "",
    videoCodec: "stored",
    width,
    height,
    averageFrameRate: frameRate,
    nominalFrameRate: frameRate,
    frameRateMode: "unknown",
    thumbnailDataUrl: "",
    audioTracks: [],
  };
}

function restartLabel(mode: EditorialRestartMode, t: T): string {
  return t(`editorial.restart.${mode}` as TranslationKey);
}

function checkpointStatusLabel(status: string, t: T): string {
  const key = {
    pending: "status.idle",
    in_progress: "status.running",
    complete: "status.succeeded",
    failed: "status.failed",
  }[status];
  return key ? t(key as TranslationKey) : status;
}

function summaryFromInspection(path: string, inspection: EditorialCheckpointInspection): EditorialCheckpointSummary {
  return {
    path,
    title: inspection.project_request.titleOrGame,
    objective: inspection.project_request.objective,
    status: inspection.artifact_status,
    sourceCount: inspection.project_request.sources.length,
    updatedAtUtc: new Date().toISOString(),
  };
}
