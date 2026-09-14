import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import ModeSelector from "./ModeSelector";

describe("long-stream availability", () => {
  it("keeps mode headers consistent and disables unavailable Local processing", () => {
    const markup = renderToStaticMarkup(
      <I18nProvider>
        <ModeSelector workflow="hosted-long-stream" onChange={vi.fn()} />
      </I18nProvider>
    );

    expect(markup).not.toContain("mode-availability-note");
    expect(markup).toMatch(/<button disabled=""[^>]*>.* Local<\/button>/s);
  });
});
