import { AlertTriangle, CheckCircle, RefreshCw } from "lucide-react";
import type { CutSilenceEncoderPreset, EncoderProbeResult } from "../../lib/types";
import SetupSection from "./SetupSection";

type Props = {
  encoder: CutSilenceEncoderPreset;
  previewHeight: 240 | 360 | 480 | 720;
  previewFps: 4 | 8 | 12 | 24;
  probes: EncoderProbeResult[];
  probing: boolean;
  renderEnabled: boolean;
  expanded: boolean;
  onToggle(): void;
  onEncoder(value: CutSilenceEncoderPreset): void;
  onPreviewHeight(value: 240 | 360 | 480 | 720): void;
  onPreviewFps(value: 4 | 8 | 12 | 24): void;
  onProbe(): void;
};

export default function CutSilenceSettingsSection(props: Props) {
  const selected = props.probes.find((probe) => probe.preset === props.encoder);
  const encoderReady = props.encoder !== "unconfigured" && Boolean(selected?.available) && !props.probing;
  const ready = !props.renderEnabled || encoderReady;
  const detail = !props.renderEnabled ? "EXO source cutting ready — optional video rendering is off" : props.probing ? "Checking encoder hardware" : encoderReady ? selected?.label ?? "Ready" : props.encoder === "unconfigured" ? "Explicit encoder selection required for rendered output" : selected?.error || "Encoder is unavailable";
  return <SetupSection title="Cut silence" detail={detail} ready={ready} expanded={props.expanded} onToggle={props.onToggle}>
    <label>
      Output encoder
      <select value={props.encoder} onChange={(event) => props.onEncoder(event.target.value as CutSilenceEncoderPreset)}>
        <option value="unconfigured">Unconfigured — choose an encoder</option>
        {props.probes.map((probe) => <option key={probe.preset} value={probe.preset} disabled={!probe.available}>{probe.label}{probe.available ? "" : " — unavailable"}</option>)}
      </select>
    </label>
    <div className="status-grid">
      {props.probes.map((probe) => <span key={probe.preset} className={probe.available ? "env-ok" : "env-missing"} title={probe.error}>{probe.available ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}{probe.label}</span>)}
    </div>
    <button onClick={props.onProbe} disabled={props.probing}><RefreshCw className={props.probing ? "spin" : ""} size={16} />{props.probing ? "Checking..." : "Recheck encoders"}</button>
    <div className="two-col">
      <label>Fallback preview resolution<select value={props.previewHeight} onChange={(event) => props.onPreviewHeight(Number(event.target.value) as Props["previewHeight"])}>{[240, 360, 480, 720].map((height) => <option key={height} value={height}>{height}p</option>)}</select></label>
      <label>Fallback preview frame rate<select value={props.previewFps} onChange={(event) => props.onPreviewFps(Number(event.target.value) as Props["previewFps"])}>{[4, 8, 12, 24].map((fps) => <option key={fps} value={fps}>{fps} fps</option>)}</select></label>
    </div>
    <div className="disabled-field">Higher preview settings use more processing time, temporary storage, playback buffering, and memory. {props.renderEnabled ? "Output: constant-frame-rate MKV with HEVC video and all audio tracks encoded as AAC at 256 kbps; the EXO references that MKV." : "Output: the EXO references the original source and AviUtl uses its default first audio track; no video is re-encoded."}</div>
  </SetupSection>;
}
