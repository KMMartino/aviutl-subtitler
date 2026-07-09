import { dialog, BrowserWindow } from "electron";

export async function chooseInputFile(window: BrowserWindow): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [
      { name: "Media", extensions: ["mkv", "mp4", "m4a", "wav", "aac", "flac", "mp3"] },
      { name: "All files", extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseFile(window: BrowserWindow): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [{ name: "All files", extensions: ["*"] }]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseGlossaryFile(window: BrowserWindow): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [
      { name: "Glossary text", extensions: ["txt"] },
      { name: "All files", extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseOutputFile(window: BrowserWindow, defaultPath?: string): Promise<string | null> {
  const result = await dialog.showSaveDialog(window, {
    defaultPath,
    filters: [
      { name: "AviUtl EXO", extensions: ["exo"] },
      { name: "All files", extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePath ?? null;
}

export async function chooseDirectory(window: BrowserWindow): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, { properties: ["openDirectory", "createDirectory"] });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseExecutable(window: BrowserWindow): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [
      { name: "Executables", extensions: ["exe"] },
      { name: "All files", extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}
