import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import type { CoreWorkflowSettings } from "../lib/types";
import AdditionalSettingsPanel from "./AdditionalSettingsPanel";

const base = {
  audioTrack: 0,
  local: { model: "", mmproj: "", llamaServer: "", cleanupModel: "", cleanupLlamaServer: "", transcriptionDraftModel: "", cleanupDraftModel: "" },
  hosted: { transcriptionProvider: "gemini", transcriptionModel: "", fallbackTranscriptionProvider: "openai", fallbackTranscriptionModel: "", cleanupProvider: "openai", cleanupModel: "", envFile: "" },
  diagnostics: { profile: false },
  additionalSettings: { youtubeChapters: false, cutSilenceMode: "automatic", renderCutVideo: false }
} as CoreWorkflowSettings;

function renderPanel(panel: React.ReactElement): string {
  return renderToStaticMarkup(<I18nProvider>{panel}</I18nProvider>);
}

describe("Cut silence additional settings", () => {
  it("shows available audio tracks without internal timing controls", () => {
    const markup = renderPanel(<AdditionalSettingsPanel
      audioTracks={[0, 1, 2].map((audioIndex) => ({ audioIndex, streamIndex: audioIndex + 1, codec: "aac", sampleRate: 48000, channels: 2, channelLayout: "stereo", language: "", title: "" }))}
      workflow="hosted-long-stream" settings={{ ...base, audioTrack: 1, editorial: { cuttingMode: "voice_gaps", gapEdgeMode: "acoustic", gameAudioTrack: 2 } }}
      encoder="unconfigured" encoderReady={false} encoderChecking={false} hasVideo frameRateMode="reported-cfr"
      onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).not.toContain('<select');
    expect(markup).not.toContain('type="number"');
    expect(markup).toContain('Track 2');
    expect(markup).toContain('Track 3');
    expect(markup).toContain('select the voice track for speech detection');
    expect(markup).not.toContain('Activity recommendations');
    expect(markup).not.toContain('type="checkbox"');
  });
  it("locks paired tracks and removes the empty-input caution", () => {
    const paired = renderPanel(<AdditionalSettingsPanel workflow="hosted-long-stream" settings={base} paired encoder="unconfigured" encoderReady={false} encoderChecking={false} hasVideo frameRateMode="reported-cfr" onConfigure={vi.fn()} onChange={vi.fn()} />);
    expect((paired.match(/<fieldset[^>]*disabled=""/g) ?? []).length).toBe(2);
    expect(paired).toContain('first voice track from the facecam');
    const empty = renderPanel(<AdditionalSettingsPanel workflow="local" settings={base} encoder="unconfigured" encoderReady={false} encoderChecking={false} hasVideo={false} frameRateMode="unknown" onConfigure={vi.fn()} onChange={vi.fn()} />);
    expect(empty).not.toContain('role="alert"');
  });
  it("defaults to EXO media cutting and warns without blocking for possible VFR", () => {
    const markup = renderPanel(<AdditionalSettingsPanel
      workflow="local" settings={base} encoder="unconfigured" encoderReady={false} encoderChecking={false}
      hasVideo frameRateMode="possible-vfr" onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).toContain("Review cuts");
    expect(markup).not.toContain("Re-encode cut video");
    expect(markup).toContain("Possible variable frame rate detected");
    expect(markup).not.toContain("Choose a Cut silence encoder");
    expect((markup.match(/<label class="check">/g) ?? []).length).toBe(2);
  });

  it("disables review when Cut silence is off without offering re-encode", () => {
    const settings = { ...base, additionalSettings: { ...base.additionalSettings!, cutSilenceMode: "off" as const } };
    const markup = renderPanel(<AdditionalSettingsPanel
      workflow="local" settings={settings} encoder="unconfigured" encoderReady={false} encoderChecking={false}
      hasVideo frameRateMode="reported-cfr" onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).not.toContain("<select");
    expect((markup.match(/type="checkbox"/g) ?? []).length).toBe(2);
    expect((markup.match(/disabled=""/g) ?? []).length).toBe(1);
  });

  it("requires encoder configuration only when rendering is selected", () => {
    const settings = { ...base, additionalSettings: { ...base.additionalSettings!, renderCutVideo: true } };
    const markup = renderPanel(<AdditionalSettingsPanel
      workflow="hosted" settings={settings} encoder="unconfigured" encoderReady={false} encoderChecking={false}
      hasVideo frameRateMode="possible-vfr" onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).toContain("Choose a Cut silence encoder");
    expect(markup).not.toContain("Possible variable frame rate detected");
  });
});
