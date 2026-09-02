import { AlertTriangle } from "lucide-react";
import type {
  CoreWorkflowSettings,
  CutSilenceEncoderPreset,
  MediaFrameRateMode,
  WorkflowName
} from "../lib/types";
import TooltipLabel from "./TooltipLabel";
import { useI18n } from "../i18n";

type Props = {
  workflow: WorkflowName;
  settings: CoreWorkflowSettings;
  encoder: CutSilenceEncoderPreset;
  encoderReady: boolean;
  encoderChecking: boolean;
  hasVideo: boolean;
  frameRateMode: MediaFrameRateMode;
  disabled?: boolean;
  onConfigure(): void;
  onChange(settings: CoreWorkflowSettings): void;
};

export default function AdditionalSettingsPanel({
  workflow, settings, encoder, encoderReady, encoderChecking, hasVideo, frameRateMode,
  disabled = false, onConfigure, onChange
}: Props) {
  const { t } = useI18n();
  const additionalSettings = settings.additionalSettings ?? {
    youtubeChapters: false,
    cutSilenceMode: "off",
    renderCutVideo: false,
    brollMode: "off"
  };
  const shortWorkflow = workflow === "local" || workflow === "hosted";
  const cutMode = additionalSettings.cutSilenceMode ?? "off";
  const cutEnabled = cutMode !== "off";
  const reviewCuts = cutMode === "review";
  const renderCutVideo = additionalSettings.renderCutVideo ?? false;
  const encoderBlocked = renderCutVideo && (!encoderReady || encoderChecking || encoder === "unconfigured");
  const updateAdditional = (next: typeof additionalSettings) => onChange({ ...settings, additionalSettings: next });

  return <section className="panel additional-settings-panel">
    <div className="panel-title">{t("additional.title")}</div>
    {shortWorkflow ? <div className="stack">
      <label className="check">
        <input disabled={disabled} type="checkbox" checked={cutEnabled} onChange={(event) => updateAdditional({ ...additionalSettings, cutSilenceMode: event.target.checked ? "automatic" : "off" })} />
        <TooltipLabel text={t("additional.cutSilenceHelp")}>{t("additional.cutSilence")}</TooltipLabel>
      </label>
      <label className="check">
        <input disabled={disabled || !cutEnabled} type="checkbox" checked={reviewCuts} onChange={(event) => updateAdditional({ ...additionalSettings, cutSilenceMode: event.target.checked ? "review" : "automatic" })} />
        <TooltipLabel text={t("additional.reviewCutsHelp")}>{t("additional.reviewCuts")}</TooltipLabel>
      </label>
      <label className="check">
        <input disabled={disabled || !cutEnabled} type="checkbox" checked={renderCutVideo} onChange={(event) => updateAdditional({ ...additionalSettings, renderCutVideo: event.target.checked })} />
        <TooltipLabel text={renderCutVideo
          ? t("additional.reencodeOnHelp")
          : t("additional.reencodeOffHelp")}>{t("additional.reencode")}</TooltipLabel>
      </label>
      {cutEnabled && <>
        {!renderCutVideo && frameRateMode === "possible-vfr" && <div className="local-blocking-alert local-advisory-alert" role="status">
          <AlertTriangle size={18} /><span><strong>{t("additional.possibleVfr")}</strong><small>{t("additional.possibleVfrDetail")}</small></span>
        </div>}
        {!renderCutVideo && frameRateMode === "unknown" && hasVideo && <div className="local-blocking-alert local-advisory-alert" role="status">
          <AlertTriangle size={18} /><span><strong>{t("additional.unknownFps")}</strong><small>{t("additional.unknownFpsDetail")}</small></span>
        </div>}
      </>}
      {cutEnabled && (!hasVideo || encoderBlocked) && <div className="local-blocking-alert" role="alert"><AlertTriangle size={18} /><span><strong>{!hasVideo ? t("additional.videoRequired") : encoderChecking ? t("additional.checkingEncoder") : encoder === "unconfigured" ? t("additional.chooseEncoder") : t("additional.encoderUnavailable")}</strong>{!hasVideo ? <small>{t("additional.selectVideo")}</small> : <button onClick={onConfigure}>{t("additional.openCutSettings")}</button>}</span></div>}
      {workflow === "hosted" && <label className="check">
        <input disabled={disabled} type="checkbox" checked={additionalSettings.youtubeChapters} onChange={(event) => updateAdditional({ ...additionalSettings, youtubeChapters: event.target.checked })} />
        <TooltipLabel text={t("additional.chaptersHelp")}>{t("additional.chapters")}</TooltipLabel>
      </label>}
      {workflow === "hosted" && <>
        <label className="check">
          <input disabled={disabled || !hasVideo} type="checkbox" checked={(additionalSettings.brollMode ?? "off") !== "off"} onChange={(event) => updateAdditional({ ...additionalSettings, brollMode: event.target.checked ? "automatic" : "off" })} />
          <TooltipLabel text={t("additional.brollHelp")}>{t("additional.broll")}</TooltipLabel>
        </label>
      </>}
    </div> : <div className="stack" />}
  </section>;
}
