import { dialog, type BrowserWindow } from "electron";
import { translate, type AppLocale } from "../shared/i18n";

export async function chooseInputFile(window: BrowserWindow, defaultPath?: string, locale: AppLocale = "en"): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    defaultPath,
    properties: ["openFile"],
    filters: [
      { name: translate(locale, "dialog.media"), extensions: ["mkv", "mp4", "m4a", "wav", "aac", "flac", "mp3"] },
      { name: translate(locale, "dialog.allFiles"), extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseInputFiles(window: BrowserWindow, defaultPath?: string, locale: AppLocale = "en"): Promise<string[] | null> {
  const result = await dialog.showOpenDialog(window, {
    defaultPath,
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: translate(locale, "dialog.editorialReview"), extensions: ["mkv", "mp4", "mov", "webm", "exo", "aup"] },
      { name: translate(locale, "dialog.allFiles"), extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths;
}

export async function chooseFile(window: BrowserWindow, locale: AppLocale = "en"): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [{ name: translate(locale, "dialog.allFiles"), extensions: ["*"] }]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseGlossaryFile(window: BrowserWindow, locale: AppLocale = "en"): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [
      { name: translate(locale, "dialog.glossaryText"), extensions: ["txt"] },
      { name: translate(locale, "dialog.allFiles"), extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseOutputFile(window: BrowserWindow, defaultPath?: string, locale: AppLocale = "en"): Promise<string | null> {
  const result = await dialog.showSaveDialog(window, {
    defaultPath,
    filters: [
      { name: translate(locale, "dialog.aviutlExo"), extensions: ["exo"] },
      { name: translate(locale, "dialog.allFiles"), extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePath ?? null;
}

export async function chooseDirectory(window: BrowserWindow): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, { properties: ["openDirectory", "createDirectory"] });
  return result.canceled ? null : result.filePaths[0] ?? null;
}

export async function chooseExecutable(window: BrowserWindow, locale: AppLocale = "en"): Promise<string | null> {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile"],
    filters: [
      { name: translate(locale, "dialog.executables"), extensions: ["exe"] },
      { name: translate(locale, "dialog.allFiles"), extensions: ["*"] }
    ]
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
}
