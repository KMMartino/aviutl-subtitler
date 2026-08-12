import { describe, expect, it } from "vitest";
import { isAppLocale, translate } from "./i18n";

describe("application localization", () => {
  it("provides complete English and Japanese catalogs", () => {
    expect(translate("en", "settings.language.label")).toBe("Application language");
    expect(translate("ja", "settings.language.label")).toBe("アプリの表示言語");
  });

  it("validates only supported application locales", () => {
    expect(isAppLocale("en")).toBe(true);
    expect(isAppLocale("ja")).toBe(true);
    expect(isAppLocale("fr")).toBe(false);
  });
});
