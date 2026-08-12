import { AlertTriangle, CheckCircle, RefreshCw } from "lucide-react";
import type { CutSilenceEncoderPreset, EncoderProbeResult } from "../../lib/types";
import SetupSection from "./SetupSection";
import { useI18n } from "../../i18n";

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
  const { t } = useI18n();
  const selected = props.probes.find((probe) => probe.preset === props.encoder);
  const encoderReady = props.encoder !== "unconfigured" && Boolean(selected?.available) && !props.probing;
  const ready = !props.renderEnabled || encoderReady;
  const detail = !props.renderEnabled ? t("settings.cut.exoOnly") : props.probing ? t("settings.cut.checkingHardware") : encoderReady ? selected?.label ?? t("common.ready") : props.encoder === "unconfigured" ? t("settings.cut.encoderRequired") : selected?.error || t("settings.cut.encoderUnavailable");
  return <SetupSection title={t("settings.cut.title")} detail={detail} ready={ready} expanded={props.expanded} onToggle={props.onToggle}>
    <label>
      {t("settings.cut.outputEncoder")}
      <select value={props.encoder} onChange={(event) => props.onEncoder(event.target.value as CutSilenceEncoderPreset)}>
        <option value="unconfigured">{t("settings.cut.unconfigured")}</option>
        {props.probes.map((probe) => <option key={probe.preset} value={probe.preset} disabled={!probe.available}>{probe.label}{probe.available ? "" : t("settings.cut.unavailableSuffix")}</option>)}
      </select>
    </label>
    <div className="status-grid">
      {props.probes.map((probe) => <span key={probe.preset} className={probe.available ? "env-ok" : "env-missing"} title={probe.error}>{probe.available ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}{probe.label}</span>)}
    </div>
    <button onClick={props.onProbe} disabled={props.probing}><RefreshCw className={props.probing ? "spin" : ""} size={16} />{props.probing ? t("settings.cut.checking") : t("settings.cut.recheck")}</button>
    <div className="two-col">
      <label>{t("settings.cut.previewResolution")}<select value={props.previewHeight} onChange={(event) => props.onPreviewHeight(Number(event.target.value) as Props["previewHeight"])}>{[240, 360, 480, 720].map((height) => <option key={height} value={height}>{height}p</option>)}</select></label>
      <label>{t("settings.cut.previewFps")}<select value={props.previewFps} onChange={(event) => props.onPreviewFps(Number(event.target.value) as Props["previewFps"])}>{[4, 8, 12, 24].map((fps) => <option key={fps} value={fps}>{fps} fps</option>)}</select></label>
    </div>
    <div className="disabled-field">{t("settings.cut.resourceNote")} {props.renderEnabled ? t("settings.cut.renderOutput") : t("settings.cut.exoOutput")}</div>
  </SetupSection>;
}
