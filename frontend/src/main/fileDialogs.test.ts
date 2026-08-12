import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BrowserWindow } from "electron";

const electron = vi.hoisted(() => ({
  showOpenDialog: vi.fn(),
  showSaveDialog: vi.fn(),
}));

vi.mock("electron", () => ({
  dialog: {
    showOpenDialog: electron.showOpenDialog,
    showSaveDialog: electron.showSaveDialog,
  },
}));

import { chooseInputFile, chooseOutputFile } from "./fileDialogs";

describe("localized native file dialogs", () => {
  const window = {} as BrowserWindow;

  beforeEach(() => {
    electron.showOpenDialog.mockReset().mockResolvedValue({ canceled: true, filePaths: [] });
    electron.showSaveDialog.mockReset().mockResolvedValue({ canceled: true });
  });

  it("uses Japanese filter labels for input media", async () => {
    await chooseInputFile(window, undefined, "ja");
    expect(electron.showOpenDialog).toHaveBeenCalledWith(window, expect.objectContaining({
      filters: [
        expect.objectContaining({ name: "メディア" }),
        expect.objectContaining({ name: "すべてのファイル" }),
      ],
    }));
  });

  it("keeps AviUtl's format name while localizing the catch-all filter", async () => {
    await chooseOutputFile(window, undefined, "ja");
    expect(electron.showSaveDialog).toHaveBeenCalledWith(window, expect.objectContaining({
      filters: [
        expect.objectContaining({ name: "AviUtl EXO" }),
        expect.objectContaining({ name: "すべてのファイル" }),
      ],
    }));
  });
});
