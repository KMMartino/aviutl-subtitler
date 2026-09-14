import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import EditorialProjectPanel from "./EditorialProjectPanel";

function renderPanel(panel: React.ReactElement): string {
  return renderToStaticMarkup(<I18nProvider>{panel}</I18nProvider>);
}

describe("Editorial project setup", () => {
  it("shows ordered sources, total duration, and the two ends of the editorial duration range", () => {
    const markup = renderPanel(<EditorialProjectPanel
      value={{
        sources: [
          single("C:\\captures\\one.mp4", 3600),
          single("C:\\captures\\two.mp4", 1800)
        ],
        titleOrGame: "Challenge run",
        objective: "Complete the game with a restriction",
        targetDurationMinSeconds: 2700,
        targetDurationMaxSeconds: 4500
      }}
      resumeCheckpoint=""
      resumeRestartFrom="compatible"
      extensionCheckpoint=""
      extensionBaseCount={0}
      reviewedProject=""
      cutApplication={null}
      onChange={vi.fn()}
      onRecoverProject={vi.fn()}
      onPrimarySource={vi.fn()}
      onResumeCheckpoint={vi.fn()}
      onBeginExtension={vi.fn()}
      onCancelExtension={vi.fn()}
      onDeclineReuse={vi.fn()}
      onReviewedProject={vi.fn()}
    />);

    expect(markup).toContain("one.mp4");
    expect(markup).toContain("two.mp4");
    expect(markup).toContain("1h 30m total");
    expect((markup.match(/type="range"/g) ?? []).length).toBe(2);
  });
});

function single(path: string, durationSeconds: number) {
  return { path, durationSeconds, mode: "single" as const, audioPath: path, visualPath: path, audioDurationSeconds: durationSeconds, visualDurationSeconds: durationSeconds, width: 1920, height: 1080, audioWidth: 1920, audioHeight: 1080, frameRate: 60, audioFrameRate: 60, pairingBasis: "single" as const, roleConfirmed: true };
}
