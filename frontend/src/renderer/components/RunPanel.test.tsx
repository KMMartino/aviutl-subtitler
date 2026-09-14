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
});
