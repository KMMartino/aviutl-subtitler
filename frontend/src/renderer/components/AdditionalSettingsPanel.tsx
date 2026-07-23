import { AlertTriangle } from "lucide-react";
import type {
  CoreWorkflowSettings,
  CutSilenceEncoderPreset,
  MediaFrameRateMode,
  WorkflowName
} from "../lib/types";
import TooltipLabel from "./TooltipLabel";

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
  const additionalSettings = settings.additionalSettings ?? {
    youtubeChapters: false,
    cutSilenceMode: "off",
    renderCutVideo: false
  };
  const shortWorkflow = workflow === "local" || workflow === "hosted";
  const cutMode = additionalSettings.cutSilenceMode ?? "off";
  const cutEnabled = cutMode !== "off";
  const reviewCuts = cutMode === "review";
  const renderCutVideo = additionalSettings.renderCutVideo ?? false;
  const encoderBlocked = renderCutVideo && (!encoderReady || encoderChecking || encoder === "unconfigured");
  const updateAdditional = (next: typeof additionalSettings) => onChange({ ...settings, additionalSettings: next });

  return <section className="panel additional-settings-panel">
    <div className="panel-title">Additional Settings</div>
    {shortWorkflow ? <div className="stack">
      <label className="check">
        <input disabled={disabled} type="checkbox" checked={cutEnabled} onChange={(event) => updateAdditional({ ...additionalSettings, cutSilenceMode: event.target.checked ? "automatic" : "off" })} />
        <TooltipLabel text="Use speech detection to remove eligible internal silence in the generated EXO. Proposed cuts shorter than 0.5 seconds are ignored.">Cut silence</TooltipLabel>
      </label>
      <label className="check">
        <input disabled={disabled || !cutEnabled} type="checkbox" checked={reviewCuts} onChange={(event) => updateAdditional({ ...additionalSettings, cutSilenceMode: event.target.checked ? "review" : "automatic" })} />
        <TooltipLabel text="Inspect every proposed cut before it is applied.">Review cuts</TooltipLabel>
      </label>
      <label className="check">
        <input disabled={disabled || !cutEnabled} type="checkbox" checked={renderCutVideo} onChange={(event) => updateAdditional({ ...additionalSettings, renderCutVideo: event.target.checked })} />
        <TooltipLabel text={renderCutVideo
          ? "Create a constant-frame-rate MKV and make the EXO reference it."
          : "Leave off to reference the original video with non-destructive AviUtl cut objects."}>Re-encode cut video</TooltipLabel>
      </label>
      {cutEnabled && <>
        <small>EXO-based cutting is intended for constant-frame-rate source video.</small>
        {!renderCutVideo && frameRateMode === "possible-vfr" && <div className="local-blocking-alert local-advisory-alert" role="status">
          <AlertTriangle size={18} /><span><strong>Possible variable frame rate detected</strong><small>Source-frame positions may not remain exact. Consider enabling Re-encode cut video.</small></span>
        </div>}
        {!renderCutVideo && frameRateMode === "unknown" && hasVideo && <div className="local-blocking-alert local-advisory-alert" role="status">
          <AlertTriangle size={18} /><span><strong>Constant frame rate could not be confirmed</strong><small>EXO cutting remains available, but re-encoding is safer for variable-frame-rate material.</small></span>
        </div>}
      </>}
      {cutEnabled && (!hasVideo || encoderBlocked) && <div className="local-blocking-alert" role="alert"><AlertTriangle size={18} /><span><strong>{!hasVideo ? "Cut silence requires a video input" : encoderChecking ? "Checking Cut silence encoder" : encoder === "unconfigured" ? "Choose a Cut silence encoder" : "The selected encoder is unavailable"}</strong>{!hasVideo ? <small>Select a media file containing video.</small> : <button onClick={onConfigure}>Open Cut silence settings</button>}</span></div>}
      {workflow === "hosted" && <label className="check">
        <input disabled={disabled} type="checkbox" checked={additionalSettings.youtubeChapters} onChange={(event) => updateAdditional({ ...additionalSettings, youtubeChapters: event.target.checked })} />
        <TooltipLabel text="Use the hosted cleanup model to analyze the full final transcript and add YouTube-style chapter title markers to the EXO output.">YouTube chapter markers</TooltipLabel>
      </label>}
    </div> : <div className="additional-settings-empty">No additional settings</div>}
  </section>;
}
