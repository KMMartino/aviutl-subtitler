import { expect, it, vi } from "vitest";
import type { BrowserWindow } from "electron";

const dialogs = vi.hoisted(() => ({ showOpenDialog: vi.fn(), showSaveDialog: vi.fn() }));
vi.mock("electron", () => ({ dialog: dialogs }));
import { chooseInputFile, chooseOutputFile } from "./fileDialogs";

it.each([
  ["input", chooseInputFile, dialogs.showOpenDialog],
  ["output", chooseOutputFile, dialogs.showSaveDialog],
] as const)("returns the %s selection, and no path after cancellation or an empty response", async (_name, choose, show) => {
  const window = {} as BrowserWindow;
  const selected = "C:/media/selected.mp4";
  for (const [response, expected] of [
    [{ canceled: false, filePaths: [selected], filePath: selected }, selected],
    [{ canceled: true, filePaths: [selected], filePath: selected }, null],
    [{ canceled: false, filePaths: [] }, null],
  ] as const) {
    show.mockResolvedValueOnce(response);
    expect(await choose(window, selected, "ja")).toBe(expected);
  }
});
