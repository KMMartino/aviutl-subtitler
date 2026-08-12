import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n";
import MediaLibraryScreen from "./MediaLibraryScreen";

describe("Media library localization", () => {
  it("renders the empty library shell in Japanese", () => {
    const markup = renderToStaticMarkup(
      <I18nProvider initialLocale="ja"><MediaLibraryScreen /></I18nProvider>,
    );
    expect(markup).toContain("メディアの場所");
    expect(markup).toContain("カタログ");
    expect(markup).toContain("アセット詳細");
    expect(markup).toContain("メディアを検索");
  });
});
