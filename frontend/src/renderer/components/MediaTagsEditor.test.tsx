import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import type { MediaAssetDetail } from "../lib/types";
import MediaTagsEditor from "./MediaTagsEditor";

describe("library tags", () => {
  it("starts collapsed with an accessible summary", () => {
    const asset = { id: "test", segments: [], structuredTags: [] } as unknown as MediaAssetDetail;
    const html = renderToStaticMarkup(<I18nProvider><MediaTagsEditor asset={asset} onSearch={vi.fn()} /></I18nProvider>);
    expect(html).toContain('<details class="library-tag-editor"><summary>');
    expect(html).not.toMatch(/<details[^>]*open/);
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<textarea");
    expect(html).not.toContain("Save tags");
  });
});
