import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import SourceUrlInput from "./SourceUrlInput";
import RunPanel from "./RunPanel";

describe("download and run controls", () => {
  it("shows the project destination and explains external file persistence", () => {
    const markup = renderToStaticMarkup(<I18nProvider><SourceUrlInput value={{ url: "https://example.com/vod", directory: "" }} defaultLocation="C:/Projects/Session/Sources" onChange={vi.fn()} /></I18nProvider>);
    expect(markup).toContain("C:/Projects/Session/Sources");
    expect(markup).toContain("keep the downloaded file when the project is deleted");
    expect(markup).toContain("Download location");
    expect(markup).not.toContain("Download recording</button>");
  });
  it("labels the single run action and preserves cancellation during download", () => {
    const markup = renderToStaticMarkup(<I18nProvider><RunPanel state="running" elapsed="0:01" canRun={false} download downloadProgress={25} onRun={vi.fn()} onCancel={vi.fn()} /></I18nProvider>);
    expect(markup).toContain("Download and run");
    expect(markup).toContain('value="25"');
    expect(markup).toMatch(/<button><svg[^]*?Cancel<\/button>/);
  });
});
