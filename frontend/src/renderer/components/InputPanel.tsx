import { FileSearch, FileVideo, LoaderCircle } from "lucide-react";
import { useState, type DragEvent } from "react";
import type { MediaAnalysis } from "../lib/types";
import TooltipLabel from "./TooltipLabel";
import { useI18n } from "../i18n";
import type { TranslationKey, TranslationParameters } from "../../shared/i18n";

type T = (key: TranslationKey, parameters?: TranslationParameters) => string;

type Props = {
  inputPath: string;
  audioTrack: number;
  analysis: MediaAnalysis | null;
  analyzing: boolean;
  analysisError: string;
  disabled?: boolean;
  onInput(path: string): void;
  onAudioTrack(value: number): void;
};

export default function InputPanel(props: Props) {
  const { t } = useI18n();
  const [dragging, setDragging] = useState(false);
  async function pickInput() {
    if (props.disabled) return;
    const path = await window.subtitler.chooseInputFile(props.inputPath || undefined);
    if (path) props.onInput(path);
  }
  function drop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    if (props.disabled) return;
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const path = window.subtitler.filePath(file);
    if (path) props.onInput(path);
  }
  const tracks = props.analysis?.audioTracks ?? [];
  const showPlaceholder = !props.analysis && !props.analyzing;
  return (
    <section className={`panel input-panel ${dragging ? "drop-active" : ""}`} onDragOver={(event) => { event.preventDefault(); if (!props.disabled) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
      <div className="panel-title"><span><FileVideo size={18} /> {t("input.title")}</span><span className="drop-hint">{t("input.dropHint")}</span></div>
      <label>
        <TooltipLabel text={t("input.fileHelp")}>{t("input.file")}</TooltipLabel>
        <div className="row">
          <input disabled={props.disabled} value={props.inputPath} onChange={(event) => props.onInput(event.target.value)} />
          <button disabled={props.disabled} aria-label={t("input.browseAria")} onClick={pickInput} title={t("input.browseAria")}><FileSearch size={17} /> {t("common.browse")}</button>
        </div>
      </label>
      <div className="media-preview">
        <div className="thumbnail-frame">
          {props.analysis?.thumbnailDataUrl
            ? <img src={props.analysis.thumbnailDataUrl} alt={t("input.thumbnailAlt")} />
            : props.analyzing
              ? <LoaderCircle className="spin" size={24} aria-label={t("input.inspecting")} />
              : <FileVideo size={28} aria-hidden="true" />}
        </div>
        <div className={`media-facts ${showPlaceholder ? "placeholder" : ""}`}>
          {props.analyzing && !props.analysis ? <strong>{t("input.inspectingProgress")}</strong> : props.analysis ? <>
            <strong>{formatDuration(props.analysis.durationSeconds, t)}</strong>
            <span>{videoDescription(props.analysis, t)}</span>
            <span>{t("input.audioTracks", { count: props.analysis.audioTracks.length, suffix: props.analysis.audioTracks.length === 1 ? "" : "s" })}</span>
          </> : <span>{props.inputPath ? t("input.waiting") : t("input.none")}</span>}
        </div>
      </div>
      {props.analysisError && <div className="field-error" role="alert">{props.analysisError}</div>}
      <label>
        <TooltipLabel text={t("input.audioTrackHelp")}>{t("input.audioTrack")}</TooltipLabel>
        {tracks.length ? (
          <select disabled={props.disabled} value={props.audioTrack} onChange={(event) => props.onAudioTrack(Number(event.target.value))}>
            {tracks.map((track) => <option key={track.streamIndex} value={track.audioIndex}>{trackLabel(track, t)}</option>)}
          </select>
        ) : <input disabled={props.disabled} type="number" min={0} value={props.audioTrack} onChange={(event) => props.onAudioTrack(Number(event.target.value))} />}
      </label>
    </section>
  );
}

function trackLabel(track: MediaAnalysis["audioTracks"][number], t: T): string {
  const title = track.title.trim();
  const titleIsRedundant = /^track\s*\d+$/i.test(title) || title.toLowerCase() === `track ${track.audioIndex + 1}`;
  const sampleRate = track.sampleRate ? `${Math.round(track.sampleRate / 100) / 10} kHz` : "";
  const details = [
    track.codec,
    track.channelLayout || (track.channels ? `${track.channels}ch` : ""),
    sampleRate,
    track.language,
    title && !titleIsRedundant ? title : ""
  ].filter(Boolean);
  return t("input.track", { number: track.audioIndex, details: details.join(" | ") });
}

function formatDuration(seconds: number | null, t: T): string {
  if (seconds === null) return t("input.unknownDuration");
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}` : `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function videoDescription(analysis: MediaAnalysis, t: T): string {
  if (!analysis.videoCodec) return `${t("input.audioOnly")} | ${analysis.formatName || t("input.unknownFormat")}`;
  const dimensions = analysis.width && analysis.height ? `${analysis.width}x${analysis.height}` : t("input.unknownSize");
  return `${analysis.videoCodec} | ${dimensions} | ${analysis.formatName || t("input.unknownFormat")}`;
}
