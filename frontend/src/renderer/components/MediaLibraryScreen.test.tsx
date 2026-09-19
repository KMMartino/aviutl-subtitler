import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n";
import MediaLibraryScreen from "./MediaLibraryScreen";

describe("library controls", () => {
  it("shows inline URL entry, icon search, and only the analysis filter", () => {
    const html = renderToStaticMarkup(<I18nProvider><MediaLibraryScreen /></I18nProvider>);
    expect(html).toContain('class="library-download"');
    expect(html).toContain('type="url"');
    expect(html).toContain('type="search"');
    expect(html.match(/<select/g)).toHaveLength(2);
    expect(html).toContain('value="analyze"');
    expect(html).toContain("Select all");
    expect(html).not.toContain("Analyze unanalyzed");
    expect(html).toContain('value="analyzed"');
    expect(html).toContain('value="unanalyzed"');
    expect(html).not.toContain('>Search</button>');
    expect(html).not.toContain('>Web</button>');
    expect(html).not.toContain('file browser');
  });
});
