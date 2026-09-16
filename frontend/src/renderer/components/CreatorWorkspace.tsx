import { ChevronDown } from "lucide-react";
import { useI18n } from "../i18n";
import { useEffect, useRef, useState } from "react";
import type { CreatorProject, ProjectCatalog, ProjectResult, TranscriptPreview } from "../../shared/creatorProject";
import { projectSourceRevision } from "../../shared/creatorProject";
import { buildEditorialSources, pairEditorialRecording, setPairedAudioRole } from "../lib/editorialPairing";
import type { EditorialSourceSelection } from "../lib/types";
import { basenameWithoutExt, dirname, joinPath } from "../lib/paths";

type Props = {
  project: CreatorProject | null;
  suggestedName?: string;
  disabled: boolean;
  selectedRecording: string;
  onResume(result: ProjectResult): Promise<void>;
  onBusyChange(busy: boolean): void;
  onProject(project: CreatorProject | null): void;
  onSelect(id: string, source: EditorialSourceSelection): void;
};

export default function CreatorWorkspace({ project, suggestedName, disabled, selectedRecording, onProject, onSelect, onBusyChange, onResume }: Props) {
  const { t, locale } = useI18n();
  const [catalog, setCatalog] = useState<ProjectCatalog | null>(null);
  const transcriptDialog = useRef<HTMLDialogElement>(null);
  const [transcript, setTranscript] = useState<TranscriptPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [pairGameplay, setPairGameplay] = useState("");
  useEffect(() => { void window.subtitler.projectCatalog().then(setCatalog).catch((error: unknown) => setError(String(error))); }, [project?.directory]);
  useEffect(() => { if (transcript && !transcriptDialog.current?.open) transcriptDialog.current?.showModal(); }, [transcript]);
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false); }, [busy, onBusyChange]);
  useEffect(() => { setName(project?.name ?? ""); }, [project?.name]);

  async function act(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  async function saveSources(sources: EditorialSourceSelection[]) {
    if (!project) return;
    const recordings = sources.map((source) => ({ id: project.recordings.find((recording) => recording.source.visualPath === source.visualPath)?.id ?? crypto.randomUUID(), source }));
    const updated = await window.subtitler.updateProject({ ...project, recordings });
    onProject(updated);
  }
  async function addSources() {
    const paths = await window.subtitler.chooseInputFiles();
    if (!paths?.length) return;
    const candidates = await Promise.all(paths.map(async (path) => ({ path, analysis: await window.subtitler.analyzeMedia(path) })));
    const sources = buildEditorialSources(candidates);
    if (sources.some((source) => !source.roleConfirmed)) throw new Error(t("project.ambiguous"));
    await saveSources([...(project?.recordings.map((recording) => recording.source) ?? []), ...sources]);
  }
  const locked = disabled || busy;
  function resultRow(result: NonNullable<typeof project>["results"][number]) {
    const output = result.deliverablePath ?? result.outputPath;
    const stale = project && (result.sourceRevision !== projectSourceRevision(project, result.recordingId) || (result.workflow === "hosted-long-stream" && result.editorialRevision !== JSON.stringify(project.editorial)));
    return <div className="creator-result" key={result.id}>
      <span>{result.workflow === "hosted-long-stream" ? t("mode.long") : t("mode.short")} · {t(`project.${result.status}`)}{stale ? ` · ${t("project.stale")}` : ""}</span>
      <small>{new Date(result.createdAt).toLocaleString(locale)}</small>
      {!result.kind && ["failed", "cancelled", "interrupted"].includes(result.status) && <button disabled={locked || Boolean(stale)} onClick={() => void act(async () => onResume(result))}>{t("project.resume")}</button>}
      <button disabled={result.status !== "complete"} onClick={() => void act(async () => {
        const file = result.workflow === "hosted-long-stream" ? output.replace(/\.json$/, ".exo") : output;
        const message = await window.subtitler.openPath(file); if (message) throw new Error(message);
      })}>{t("project.openResult")}</button>
      <button onClick={() => void window.subtitler.showItemInFolder(output)}>{t("project.files")}</button>
      {project && result.status === "complete" && <button disabled={locked} onClick={() => void act(async () => window.subtitler.showItemInFolder(await window.subtitler.exportProjectExo(project.directory, result.id)))}>{t("project.export")}</button>}
      {project && result.transcripts?.map((file, index) => <button key={file} onClick={() => void act(async () => setTranscript(await window.subtitler.projectTranscript(project.directory, result.id, file)))}>{t("project.transcript")}{result.transcripts!.length > 1 ? ` ${index + 1}` : ""}</button>)}
    </div>;
  }
  return <section className="creator-workspace" aria-label={t("project.workspace")}>
    <button className="creator-summary" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><strong>{project?.name || suggestedName || t("project.new")}</strong><ChevronDown size={18} className={expanded ? "expanded" : ""} /></button>
    {expanded && <><div className="creator-toolbar">
      <strong>{t("project.workspace")}</strong>
      {project && <><input aria-label={t("project.name")} value={name} disabled={locked} onChange={(event) => setName(event.target.value)} onBlur={() => { if (name.trim() && name !== project.name) void act(async () => onProject(await window.subtitler.updateProject({ ...project, name }))); }} />
        <button disabled={locked} onClick={() => onProject(null)}>{t("project.close")}</button>
        <button disabled={locked} onClick={() => void act(async () => { if (await window.subtitler.deleteProject(project.directory)) onProject(null); })}>{t("project.delete")}</button>
        <button disabled={locked} onClick={() => void act(async () => onProject(await window.subtitler.createProject(t("project.untitled"))))}>{t("project.new")}</button>
        <button disabled={locked} onClick={() => void act(async () => { const directory = await window.subtitler.chooseDirectory(); if (directory) onProject(await window.subtitler.openProject(directory)); })}>{t("project.open")}</button>
        <button onClick={() => void window.subtitler.openPath(project.directory)}>{t("project.folder")}</button></>}
      {!project && <>
        <input aria-label={t("project.name")} placeholder={t("project.newName")} value={name} onChange={(event) => setName(event.target.value)} />
        <button disabled={locked} onClick={() => void act(async () => onProject(await window.subtitler.createProject(name || t("project.untitled"))))}>{t("project.new")}</button>
        <button disabled={locked} onClick={() => void act(async () => { const directory = await window.subtitler.chooseDirectory(); if (directory) onProject(await window.subtitler.openProject(directory)); })}>{t("project.open")}</button>
        <button disabled={locked} onClick={() => void act(async () => { const directory = await window.subtitler.chooseDirectory(); if (directory) onProject(await window.subtitler.createProject(name || t("project.untitled"), directory)); })}>{t("project.newLocation")}</button>
        <button disabled={locked} onClick={() => void act(async () => { const directory = await window.subtitler.chooseDirectory(); if (directory) setCatalog(await window.subtitler.setProjectDirectory(directory)); })}>{t("project.defaultLocation")}</button>
      </>}
    </div>
    {project && <div className="creator-toolbar">
      <small>{t("project.outputFolder")}: {project.outputDirectory || (project.recordings[0] ? joinPath(dirname(project.recordings[0].source.visualPath), basenameWithoutExt(project.recordings[0].source.visualPath)) : t("project.besideMedia"))}</small>
      <button disabled={locked} onClick={() => void act(async () => { const outputDirectory = await window.subtitler.chooseDirectory(); if (outputDirectory) onProject(await window.subtitler.updateProject({ ...project, outputDirectory })); })}>{t("project.changeOutput")}</button>
      {project.outputDirectory && <button disabled={locked} onClick={() => void act(async () => onProject(await window.subtitler.updateProject({ ...project, outputDirectory: "" })))}>{t("project.mediaFolder")}</button>}
    </div>}
    {!project && <div className="creator-home"><small>{t("project.workspace")}: {catalog?.defaultDirectory}</small><p>{t("project.startHint")}</p>{catalog?.recent.map((item) => <button disabled={locked} key={item.directory} title={item.directory} onClick={() => void act(async () => onProject(await window.subtitler.openProject(item.directory)))}>{item.name}</button>)}</div>}
    {project && <div className="creator-project-sections" key={project.directory}><details>
      <summary>{t("project.recordings")} ({project.recordings.length})</summary>
      <div className="creator-toolbar"><strong>{t("project.recordings")}</strong><button disabled={locked} onClick={() => void act(addSources)}>{t("project.addRecordings")}</button>
        <button disabled={locked} onClick={() => void act(async () => { const selected = await window.subtitler.chooseInputFile(); if (selected) setPairGameplay(selected); })}>{t("project.addPair")}</button>
      </div>
      {pairGameplay && <div className="creator-toolbar"><small>{t("project.gameplay")}: {pairGameplay.split(/[\\/]/).pop()}</small><button disabled={locked} onClick={() => void act(async () => {
        const facecam = await window.subtitler.chooseInputFile(); if (!facecam) return;
        const [gameAnalysis, faceAnalysis] = await Promise.all([window.subtitler.analyzeMedia(pairGameplay), window.subtitler.analyzeMedia(facecam)]);
        await saveSources([...project.recordings.map((recording) => recording.source), pairEditorialRecording({ path: pairGameplay, analysis: gameAnalysis }, { path: facecam, analysis: faceAnalysis })]); setPairGameplay("");
      })}>{t("project.chooseFace")}</button><button onClick={() => setPairGameplay("")}>{t("project.cancelPair")}</button></div>}
      {project.recordings.map((recording, index) => <div className="creator-recording" key={recording.id}>
        <button className={selectedRecording === recording.id ? "active" : ""} disabled={locked} onClick={() => onSelect(recording.id, recording.source)}>{index + 1}. {recording.source.visualPath.split(/[\\/]/).pop()}</button>
        <small>{recording.source.mode === "paired" ? t("project.paired", { name: recording.source.audioPath.split(/[\\/]/).pop() ?? "" }) : t("project.single")}</small>
        {recording.mediaIdentity && Object.values(recording.mediaIdentity).some((identity) => identity === null) && <small role="alert">{t("project.missing")}</small>}
        {recording.source.mode === "paired" && <label>{t("project.speech")} <select disabled={locked} value={recording.source.speechSource ?? "facecam"} onChange={(event) => { const speechSource = event.target.value as "facecam" | "gameplay"; void act(async () => saveSources(project.recordings.map((item) => item.id === recording.id ? { ...item.source, speechSource } : item.source))); }}><option value="facecam">{t("project.faceDefault")}</option><option value="gameplay">{t("project.gameplay")}</option></select></label>}
        {recording.source.mode === "paired" && <button disabled={locked} onClick={() => void act(async () => saveSources(project.recordings.map((item) => item.id === recording.id ? setPairedAudioRole(item.source, item.source.visualPath) : item.source)))}>{t("project.swap")}</button>}
        <button disabled={locked || index === 0} aria-label={t("editorial.moveEarlier")} onClick={() => void act(async () => { const sources = project.recordings.map((item) => item.source); [sources[index - 1], sources[index]] = [sources[index], sources[index - 1]]; await saveSources(sources); })}>↑</button>
        <button disabled={locked} onClick={() => void act(async () => saveSources(project.recordings.filter((item) => item.id !== recording.id).map((item) => item.source)))}>{t("project.remove")}</button>
      </div>)}
      {!project.recordings.length && <p>{t("project.addHint")}</p>}
    </details>
      <details><summary>{t("project.history", { count: project.results.length })}</summary>{project.results.slice().reverse().map(resultRow)}{!project.results.length && <p>{t("project.emptyResults")}</p>}</details>
    </div>}
    </>}
    {transcript && <dialog ref={transcriptDialog} onCancel={() => setTranscript(null)} className="creator-transcript" aria-label={t("project.transcript")}><div className="creator-toolbar"><strong>{t("project.transcript")}</strong><button autoFocus onClick={() => setTranscript(null)}>{t("project.closeTranscript")}</button></div><p>{transcript.source}</p>{transcript.segments.map((segment, index) => <p key={index}><small>{segment.start.toFixed(2)}–{segment.end.toFixed(2)}</small><br />{segment.text}</p>)}</dialog>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
