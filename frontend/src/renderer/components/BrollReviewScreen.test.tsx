import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import BrollReviewScreen from "./BrollReviewScreen";

describe("B-roll review localization", () => {
  it("renders the empty review state in Japanese", () => {
    const markup = renderToStaticMarkup(
      <I18nProvider initialLocale="ja">
        <BrollReviewScreen runId="run-1" reviewId="review-1" candidates={[]} onSubmit={vi.fn()} onCancel={vi.fn()} />
      </I18nProvider>,
    );
    expect(markup).toContain("確認が必要なファイル名のみ一致の B ロールはありません");
  });
});
