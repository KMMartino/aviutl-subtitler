import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import SilenceReviewScreen from "./SilenceReviewScreen";

describe("Silence review screen", () => {
  it("presents the three required unresolved decisions and blocks submission", () => {
    const markup = renderToStaticMarkup(<SilenceReviewScreen
      runId="run-1"
      reviewId="review-1"
      candidates={[{ id: "silence-0001", silenceStart: 2, silenceEnd: 7, cutStart: 2.5, cutEnd: 6.8, cutDuration: 4.3 }]}
      onSubmit={vi.fn()}
      onCancel={vi.fn()}
    />);
    expect(markup).toContain("Accept cut");
    expect(markup).toContain("Reject cut");
    expect(markup).toContain("Mark and reject");
    expect(markup).toContain("Submit decisions");
    expect(markup).toContain("silence-review-navigation");
    expect(markup).toMatch(/disabled=""[^>]*>Submit decisions|disabled=""/);
  });
});
