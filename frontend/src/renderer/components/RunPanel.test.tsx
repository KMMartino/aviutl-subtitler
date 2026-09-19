import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import RunPanel from "./RunPanel";

describe("run readiness", () => {
  it("explains a blocked run and removes that explanation when ready", () => {
    const render = (canRun: boolean) => renderToStaticMarkup(<I18nProvider><RunPanel state="idle" elapsed="00:00" canRun={canRun} blockedReason="Install Python requirements" onConfigure={vi.fn()} onRun={vi.fn()} onCancel={vi.fn()} /></I18nProvider>);
    const blocked = render(false);
    expect(blocked).toContain('role="status"');
    expect(blocked).toContain("Install Python requirements");
    expect(blocked).toContain("Open settings");
    expect(blocked).toMatch(/class="primary" disabled=""/);
    const ready = render(true);
    expect(ready).not.toContain("Install Python requirements");
    expect(ready).not.toMatch(/class="primary" disabled/);
  });
  it("keeps a disabled run quiet when no missing dependency is reported", () => {
    const html = renderToStaticMarkup(<I18nProvider><RunPanel state="idle" elapsed="00:00" canRun={false} onConfigure={vi.fn()} onRun={vi.fn()} onCancel={vi.fn()} /></I18nProvider>);
    expect(html).not.toContain("run-blocker");
    expect(html).not.toContain("Open settings");
    expect(html).not.toContain('role="group"');
  });

  it("shows the subtitle processing mode and locks it during a run", () => {
    const html = renderToStaticMarkup(<I18nProvider><RunPanel state="running" elapsed="00:01" canRun processingMode="hosted" onProcessingMode={vi.fn()} onRun={vi.fn()} onCancel={vi.fn()} /></I18nProvider>);
    expect(html).toContain('role="group"');
    expect(html).toMatch(/disabled="" aria-pressed="false"[^>]*>Local/);
    expect(html).toMatch(/disabled="" aria-pressed="true"[^>]*>Hosted/);
  });

});
