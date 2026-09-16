import { forwardRef, useImperativeHandle, useEffect, useState, type DragEvent } from "react";
import SourceUrlInput, { type SourceDownload } from "./SourceUrlInput";
import { FileVideo } from "lucide-react";
import { useI18n } from "../i18n";
import { formatTimecode, parseTimecode } from "../lib/timecodes";
import type { MediaAnalysis, MomentExtractionRequest } from "../lib/types";

export type MomentDraft = { sourcePath: string; facecamPath: string; speechSource: "gameplay" | "facecam";
  query: string; start: string; end: string; origin: number; sourceUrl?: string; boundedEnd: boolean; url: string; audioTrack: number };
export type MomentExtractionHandle = { download(directory: string): Promise<MomentExtractionRequest> };
type Props = { defaultDownloadLocation: string; download: SourceDownload; onDownload(value: SourceDownload): void; disabled: boolean; initial: MomentDraft | null; onBusy(busy: boolean): void;
  onDraft(value: MomentDraft): void;
  onReady(value: MomentExtractionRequest | null, audioTrack: number): void };

export default forwardRef<MomentExtractionHandle, Props>(function MomentExtractionPanel({ disabled, initial, onBusy, onReady, onDraft, download: sourceDownload, onDownload, defaultDownloadLocation }: Props, ref) {
  const { t, locale } = useI18n();
  const [sourcePath, setSourcePath] = useState(initial?.sourcePath ?? "");
  const [facecamPath, setFacecamPath] = useState(initial?.facecamPath ?? "");
  const [speechSource, setSpeechSource] = useState<"gameplay" | "facecam">(initial?.speechSource ?? "gameplay");
  const [query, setQuery] = useState(initial?.query ?? "");
  const [start, setStart] = useState(initial?.start ?? "");
  const [end, setEnd] = useState(initial?.end ?? "");
  const [origin, setOrigin] = useState(initial?.origin ?? 0);
  const [sourceUrl, setSourceUrl] = useState(initial?.sourceUrl);
  const [boundedEnd, setBoundedEnd] = useState(initial?.boundedEnd ?? false);
  const url = sourceDownload.url;
  const setUrl = (url: string) => onDownload({ ...sourceDownload, url });
  const [analysis, setAnalysis] = useState<MediaAnalysis | null>(null);
  const [speechAnalysis, setSpeechAnalysis] = useState<MediaAnalysis | null>(null);
  const [audioTrack, setAudioTrack] = useState(initial?.audioTrack ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragTarget, setDragTarget] = useState<"recording" | "facecam" | null>(null);
  useEffect(() => { onDraft({ sourcePath, facecamPath, speechSource, query, start, end, origin, sourceUrl, boundedEnd, url, audioTrack }); },
    [sourcePath, facecamPath, speechSource, query, start, end, origin, sourceUrl, boundedEnd, url, audioTrack, onDraft]);
  useEffect(() => {
    let active = true;
    setAnalysis(null); setSpeechAnalysis(null);
    if (sourcePath) void Promise.all([window.subtitler.analyzeMedia(sourcePath),
      window.subtitler.analyzeMedia(facecamPath && speechSource === "facecam" ? facecamPath : sourcePath)])
      .then(([video, speech]) => { if (active) { setAnalysis(video); setSpeechAnalysis(speech); setAudioTrack((track) => track < speech.audioTracks.length ? track : 0); } })
      .catch((cause: unknown) => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [sourcePath, facecamPath, speechSource]);
  let rangeError = "";
  let startSec = 0;
  let endSec: number | undefined;
  try {
    startSec = parseTimecode(start, 0) / 1000;
    endSec = end.trim() ? parseTimecode(end) / 1000 : undefined;
    if (endSec !== undefined && endSec <= startSec || !url.trim() && analysis?.durationSeconds && (startSec >= analysis.durationSeconds || endSec !== undefined && endSec > analysis.durationSeconds + .05)) throw new Error(t("moments.invalidRange"));
  } catch (cause) { rangeError = String(cause instanceof Error ? cause.message : cause); }
  useEffect(() => {
    onReady(!busy && !rangeError && query.trim() && (url.trim() || sourcePath && analysis?.videoCodec) ? {
      sourcePath, facecamPath: facecamPath || undefined, speechSource, query: query.trim(), startSec, endSec,
      originOffsetSec: origin, sourceUrl, boundedEnd, locale,
    } : null, audioTrack);
  }, [busy, url, rangeError, sourcePath, facecamPath, speechSource, query, startSec, endSec, origin, sourceUrl, boundedEnd, locale, audioTrack, analysis, sourceDownload.directory, onReady]);
  function selectFile(file: string, face: boolean) {
    setError("");
    if (face) { setFacecamPath(file); setSpeechSource("facecam"); }
    else { setSourcePath(file); setOrigin(0); setSourceUrl(undefined); setBoundedEnd(false); setStart(""); setEnd(""); setUrl(""); }
  }
  function dropFile(event: DragEvent, face: boolean) {
    event.preventDefault();
    event.stopPropagation();
    setDragTarget(null);
    if (disabled || busy) return;
    const file = event.dataTransfer.files[0];
    if (!file) return;
    try { selectFile(window.subtitler.filePath(file), face); }
    catch (cause) { setError(String(cause)); }
  }
  async function choose(face: boolean) {
    try {
      const file = await window.subtitler.chooseInputFile();
      if (!file) return;
      selectFile(file, face);
    } catch (cause) { setError(String(cause)); }
  }
  async function download(directory: string): Promise<MomentExtractionRequest> {
    setBusy(true); onBusy(true); setError("");
    try {
      const acquired = await window.subtitler.acquireSource(url.trim(), start.trim() || end.trim() ? { startSec, endSec } : undefined, directory);
      setSourcePath(acquired.path); setFacecamPath(""); setSpeechSource("gameplay");
      setOrigin(acquired.sourceStartSec); setSourceUrl(acquired.origin.sourcePageUrl);
      setBoundedEnd(acquired.sourceEndSec < (acquired.origin.durationSec ?? acquired.sourceEndSec));
      setStart(""); setEnd(""); setUrl("");
      return { sourcePath: acquired.path, speechSource: "gameplay", query: query.trim(), startSec: 0,
        originOffsetSec: acquired.sourceStartSec, sourceUrl: acquired.origin.sourcePageUrl,
        boundedEnd: acquired.sourceEndSec < (acquired.origin.durationSec ?? acquired.sourceEndSec), locale };
    } catch (cause) { setError(String(cause)); throw cause; }
    finally { setBusy(false); onBusy(false); }
  }
  useImperativeHandle(ref, () => ({ download }));
  return <section className={`panel input-panel moment-input-panel${dragTarget === "recording" ? " drop-active" : ""}`}
    onDragOver={(event) => { event.preventDefault(); if (!disabled && !busy) setDragTarget("recording"); }}
    onDragLeave={() => setDragTarget(null)} onDrop={(event) => dropFile(event, false)}>
    <div className="panel-title"><span><FileVideo size={18} /> {t("moments.title")}</span></div>
    <p>{t("moments.help")}</p>
    <div className="moment-two-col">
      {([false, true] as const).map((face) => <div key={String(face)} className="moment-source">
        <button className={dragTarget === (face ? "facecam" : "recording") ? "drop-active" : ""} disabled={disabled || busy}
          onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); if (!disabled && !busy) setDragTarget(face ? "facecam" : "recording"); }}
          onDragLeave={() => setDragTarget(null)} onDrop={(event) => dropFile(event, face)} onClick={() => void choose(face)}>
          <span>{t(face ? "moments.facecam" : "moments.choose")}</span><small>{t("input.dropHint")}</small>
        </button>
        {(face ? facecamPath : sourcePath) && <span className="moment-path">{face ? facecamPath : sourcePath}</span>}
        {face && facecamPath && <button disabled={disabled || busy} onClick={() => setFacecamPath("")}>{t("moments.removeFacecam")}</button>}
      </div>)}
    </div>
    <SourceUrlInput disabled={disabled || busy} defaultLocation={defaultDownloadLocation} value={sourceDownload} onChange={onDownload} />
    {facecamPath && <label>{t("project.speech")}<select disabled={disabled || busy} value={speechSource} onChange={(event) => setSpeechSource(event.target.value as "gameplay" | "facecam")}><option value="gameplay">{t("project.gameplay")}</option><option value="facecam">{t("project.faceDefault")}</option></select></label>}
    {speechAnalysis && speechAnalysis.audioTracks.length > 1 && <label>{t("moments.audioTrack")}<select disabled={disabled || busy} value={audioTrack} onChange={(event) => setAudioTrack(Number(event.target.value))}>{speechAnalysis.audioTracks.map((_, index) => <option key={index} value={index}>{index + 1}</option>)}</select></label>}
    <label>{t("moments.query")}<textarea disabled={disabled || busy} rows={3} maxLength={20000} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("moments.queryHint")} /></label>
    <div className="moment-two-col"><label>{t("moments.start")}<input disabled={disabled || busy} value={start} placeholder="00:00:00" onChange={(event) => setStart(event.target.value)} /></label>
      <label>{t("moments.end")}<input disabled={disabled || busy} value={end} placeholder={t("moments.toEnd")} onChange={(event) => setEnd(event.target.value)} /></label></div>
    <small>{t("moments.rangeHelp")}{analysis?.durationSeconds ? ` · ${formatTimecode(analysis.durationSeconds * 1000)}` : ""}</small>
    {origin > 0 && <small>{t("moments.origin", { time: formatTimecode(origin * 1000) })}</small>}
    {(error || rangeError) && <p className="field-error" role="alert">{error || rangeError}</p>}
  </section>;
});
