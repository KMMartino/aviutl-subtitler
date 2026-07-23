import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CoreWorkflowSettings } from "../lib/types";
import AdditionalSettingsPanel from "./AdditionalSettingsPanel";

const base = {
  audioTrack: 0,
  local: { model: "", mmproj: "", llamaServer: "", cleanupModel: "", cleanupLlamaServer: "", transcriptionDraftModel: "", cleanupDraftModel: "" },
  hosted: { transcriptionProvider: "gemini", transcriptionModel: "", fallbackTranscriptionProvider: "openai", fallbackTranscriptionModel: "", cleanupProvider: "openai", cleanupModel: "", envFile: "" },
  diagnostics: { profile: false },
  additionalSettings: { youtubeChapters: false, cutSilenceMode: "automatic", renderCutVideo: false }
} as CoreWorkflowSettings;

describe("Cut silence additional settings", () => {
  it("defaults to EXO media cutting and warns without blocking for possible VFR", () => {
    const markup = renderToStaticMarkup(<AdditionalSettingsPanel
      workflow="local" settings={base} encoder="unconfigured" encoderReady={false} encoderChecking={false}
      hasVideo frameRateMode="possible-vfr" onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).toContain("Review cuts");
    expect(markup).toContain("Re-encode cut video");
    expect(markup).toContain("Proposed cuts shorter than 0.5 seconds are ignored.");
    expect(markup).toContain("non-destructive AviUtl cut objects");
    expect(markup).toContain("Possible variable frame rate detected");
    expect(markup).not.toContain("Choose a Cut silence encoder");
    expect((markup.match(/<label class="check">/g) ?? []).length).toBe(3);
    expect(markup).not.toContain("additional-setting-toggle");
  });

  it("shows review and re-encode as disabled checkboxes when Cut silence is off", () => {
    const settings = { ...base, additionalSettings: { ...base.additionalSettings!, cutSilenceMode: "off" as const } };
    const markup = renderToStaticMarkup(<AdditionalSettingsPanel
      workflow="local" settings={settings} encoder="unconfigured" encoderReady={false} encoderChecking={false}
      hasVideo frameRateMode="reported-cfr" onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).not.toContain("<select");
    expect((markup.match(/type="checkbox"/g) ?? []).length).toBe(3);
    expect((markup.match(/disabled=""/g) ?? []).length).toBe(2);
  });

  it("requires encoder configuration only when rendering is selected", () => {
    const settings = { ...base, additionalSettings: { ...base.additionalSettings!, renderCutVideo: true } };
    const markup = renderToStaticMarkup(<AdditionalSettingsPanel
      workflow="hosted" settings={settings} encoder="unconfigured" encoderReady={false} encoderChecking={false}
      hasVideo frameRateMode="possible-vfr" onConfigure={vi.fn()} onChange={vi.fn()}
    />);
    expect(markup).toContain("Choose a Cut silence encoder");
    expect(markup).not.toContain("Possible variable frame rate detected");
  });
});
