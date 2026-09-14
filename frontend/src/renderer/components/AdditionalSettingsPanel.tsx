import { supportsWorkflowFeature } from "../../shared/workflowCatalog";
import { AlertTriangle } from "lucide-react";
import type {
  CoreWorkflowSettings,
  AudioTrackInfo,
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
  audioTracks?: AudioTrackInfo[];
  paired?: boolean;
  frameRateMode: MediaFrameRateMode;
  disabled?: boolean;
  onConfigure(): void;
  onChange(settings: CoreWorkflowSettings): void;
};

export default function AdditionalSettingsPanel({
  workflow, settings, encoder, encoderReady, encoderChecking, hasVideo, frameRateMode, audioTracks = [], paired = false,
  disabled = false, onConfigure, onChange
}: Props) {
  const { t } = useI18n();
  const additionalSettings = settings.additionalSettings ?? {
    youtubeChapters: false,
    cutSilenceMode: "off",
    renderCutVideo: false,
    brollMode: "off"
  };
  const shortWorkflow = supportsWorkflowFeature(workflow, "silence");
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
      {cutEnabled && hasVideo && encoderBlocked && <div className="local-blocking-alert" role="alert"><AlertTriangle size={18} /><span><strong>{encoderChecking ? t("additional.checkingEncoder") : encoder === "unconfigured" ? t("additional.chooseEncoder") : t("additional.encoderUnavailable")}</strong><button onClick={onConfigure}>{t("additional.openCutSettings")}</button></span></div>}
      {supportsWorkflowFeature(workflow, "chapters") && <label className="check">
        <input disabled={disabled} type="checkbox" checked={additionalSettings.youtubeChapters} onChange={(event) => updateAdditional({ ...additionalSettings, youtubeChapters: event.target.checked })} />
        <TooltipLabel text={t("additional.chaptersHelp")}>{t("additional.chapters")}</TooltipLabel>
      </label>}
      {supportsWorkflowFeature(workflow, "broll") && <>
        <label className="check">
          <input disabled={disabled || !hasVideo} type="checkbox" checked={(additionalSettings.brollMode ?? "off") !== "off"} onChange={(event) => updateAdditional({ ...additionalSettings, brollMode: event.target.checked ? "automatic" : "off" })} />
          <TooltipLabel text={t("additional.brollHelp")}>{t("additional.broll")}</TooltipLabel>
        </label>
      </>}
    </div> : <div className="stack">
      <fieldset className="audio-track-choice" disabled={disabled || paired || !audioTracks.length}>
        <legend><TooltipLabel text={t("additional.trackTooltip")}>{t("additional.speechTrack")}</TooltipLabel></legend>
        <div className="segmented track-options">{(paired ? [{ audioIndex: 0, title: "" }] : audioTracks).map((track) => <label className={settings.audioTrack === track.audioIndex || paired ? "active" : ""} key={track.audioIndex}>
          <input type="radio" name="editorial-voice-track" checked={paired ? track.audioIndex === 0 : settings.audioTrack === track.audioIndex} onChange={() => onChange({ ...settings, audioTrack: track.audioIndex })} />
          {track.title || t("additional.trackNumber", { number: track.audioIndex + 1 })}
        </label>)}</div>
      </fieldset>
      <fieldset className="audio-track-choice" disabled={disabled || paired || !audioTracks.length}>
        <legend>{t("additional.gameTrack")}</legend>
        <div className="segmented track-options">
          {!paired && <label className={settings.editorial?.gameAudioTrack === undefined ? "active" : ""}><input type="radio" name="editorial-game-track" checked={settings.editorial?.gameAudioTrack === undefined} onChange={() => onChange({ ...settings, editorial: { ...settings.editorial, cuttingMode: "voice_gaps", gameAudioTrack: undefined } })} />{t("additional.noGameTrack")}</label>}
          {(paired ? [{ audioIndex: 0, title: "" }] : audioTracks).map((track) => <label className={settings.editorial?.gameAudioTrack === track.audioIndex || paired ? "active" : ""} key={track.audioIndex}>
            <input type="radio" name="editorial-game-track" checked={paired ? track.audioIndex === 0 : settings.editorial?.gameAudioTrack === track.audioIndex} onChange={() => onChange({ ...settings, editorial: { ...settings.editorial, cuttingMode: "voice_gaps", gameAudioTrack: track.audioIndex } })} />
            {track.title || t("additional.trackNumber", { number: track.audioIndex + 1 })}
          </label>)}
        </div>
      </fieldset>
      {paired && <small className="field-help">{t("additional.pairedTracks")}</small>}
    </div>}
  </section>;
}
