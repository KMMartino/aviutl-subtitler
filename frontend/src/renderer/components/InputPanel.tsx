import { FileVideo, FolderOpen, LoaderCircle } from "lucide-react";
import { useState, type DragEvent } from "react";
import type { MediaAnalysis } from "../lib/types";
import TooltipLabel from "./TooltipLabel";

type Props = {
  inputPath: string;
  audioTrack: number;
  analysis: MediaAnalysis | null;
  analyzing: boolean;
  analysisError: string;
  onInput(path: string): void;
  onAudioTrack(value: number): void;
};

export default function InputPanel(props: Props) {
  const [dragging, setDragging] = useState(false);
  async function pickInput() {
    const path = await window.subtitler.chooseInputFile();
    if (path) props.onInput(path);
  }
  function drop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) props.onInput(window.subtitler.filePath(file));
  }
  const tracks = props.analysis?.audioTracks ?? [];
  return (
    <section className={`panel input-panel ${dragging ? "drop-active" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
      <div className="panel-title"><span><FileVideo size={18} /> Input</span><span className="drop-hint">Drop media here</span></div>
      <label>
        <TooltipLabel text="Video or audio file to transcribe. You can also drag a media file onto this panel.">Input file</TooltipLabel>
        <div className="row">
          <input value={props.inputPath} onChange={(event) => props.onInput(event.target.value)} />
          <button className="icon-button" onClick={pickInput} title="Choose input file"><FolderOpen size={17} /></button>
        </div>
      </label>
      {(props.analysis || props.analyzing) && (
        <div className="media-preview">
          <div className="thumbnail-frame">
            {props.analysis?.thumbnailDataUrl
              ? <img src={props.analysis.thumbnailDataUrl} alt="Selected media thumbnail" />
              : props.analyzing
                ? <LoaderCircle className="spin" size={24} />
                : <FileVideo size={28} />}
          </div>
          <div className="media-facts">
            {props.analyzing && !props.analysis ? <strong>Inspecting media...</strong> : props.analysis && <>
              <strong>{formatDuration(props.analysis.durationSeconds)}</strong>
              <span>{videoDescription(props.analysis)}</span>
              <span>{props.analysis.audioTracks.length} audio track{props.analysis.audioTracks.length === 1 ? "" : "s"}</span>
              <div className="track-list">
                {props.analysis.audioTracks.map((track) => <small key={track.streamIndex}>{trackLabel(track)}</small>)}
              </div>
            </>}
          </div>
        </div>
      )}
      {props.analysisError && <div className="field-error">{props.analysisError}</div>}
      <label>
        <TooltipLabel text="Audio stream passed to FFmpeg. Track codec, language, and channel information is read automatically when media is selected.">Audio track</TooltipLabel>
        {tracks.length ? (
          <select value={props.audioTrack} onChange={(event) => props.onAudioTrack(Number(event.target.value))}>
            {tracks.map((track) => <option key={track.streamIndex} value={track.audioIndex}>{trackLabel(track)}</option>)}
          </select>
        ) : <input type="number" min={0} value={props.audioTrack} onChange={(event) => props.onAudioTrack(Number(event.target.value))} />}
      </label>
    </section>
  );
}

function trackLabel(track: MediaAnalysis["audioTracks"][number]): string {
  const details = [track.codec, track.channelLayout || (track.channels ? `${track.channels}ch` : ""), track.language, track.title].filter(Boolean);
  return `Track ${track.audioIndex}: ${details.join(" | ")}`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "Unknown duration";
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}` : `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function videoDescription(analysis: MediaAnalysis): string {
  if (!analysis.videoCodec) return `Audio only | ${analysis.formatName || "unknown format"}`;
  const dimensions = analysis.width && analysis.height ? `${analysis.width}x${analysis.height}` : "unknown size";
  return `${analysis.videoCodec} | ${dimensions} | ${analysis.formatName || "unknown format"}`;
}
