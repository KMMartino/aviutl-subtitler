import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import ModeSelector from "./ModeSelector";

describe("long-stream availability", () => {
  it("explains that long streams require hosted models and disables Local", () => {
    const markup = renderToStaticMarkup(
      <I18nProvider>
        <ModeSelector workflow="hosted-long-stream" onChange={vi.fn()} />
      </I18nProvider>
    );

    expect(markup).toContain("Local model + long stream is not available");
    expect(markup).toMatch(/<button disabled=""[^>]*>.* Local<\/button>/s);
  });
});
