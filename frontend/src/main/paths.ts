import { app } from "electron";
import path from "node:path";

export type RuntimePaths = {
  appResourceRoot: string;
  bundledBackendRoot: string;
  bundledConfigRoot: string;
  userDataRoot: string;
  stateRoot: string;
  userConfigRoot: string;
  userToolsRoot: string;
  userModelsRoot: string;
  managedPythonRoot: string;
  managedFfmpegRoot: string;
  managedYtDlpRoot: string;
  mediaLibraryRoot: string;
  mediaLibraryDatabase: string;
  managedMediaRoot: string;
  managedWebMediaRoot: string;
  envFile: string;
  glossaryFile: string;
};

export function isPackagedApp(): boolean {
  return app.isPackaged;
}

export function appResourceRoot(): string {
  return isPackagedApp() ? process.resourcesPath : repoRoot();
}

export function userDataRoot(): string {
  return isPackagedApp() ? app.getPath("userData") : path.join(repoRoot(), ".frontend-state");
}

export function runtimePaths(): RuntimePaths {
  const resources = appResourceRoot();
  const userData = userDataRoot();
  const backendRoot = isPackagedApp() ? path.join(resources, "app-backend") : resources;
  const stateRoot = userData;
  const userToolsRoot = path.join(stateRoot, "tools");
  return {
    appResourceRoot: resources,
    bundledBackendRoot: backendRoot,
    bundledConfigRoot: path.join(backendRoot, "configs"),
    userDataRoot: userData,
    stateRoot,
    userConfigRoot: path.join(stateRoot, "configs"),
    userToolsRoot,
    userModelsRoot: path.join(stateRoot, "models"),
    managedPythonRoot: path.join(stateRoot, "python"),
    managedFfmpegRoot: path.join(userToolsRoot, "ffmpeg"),
    managedYtDlpRoot: path.join(userToolsRoot, "yt-dlp"),
    mediaLibraryRoot: path.join(stateRoot, "media-library"),
    mediaLibraryDatabase: path.join(stateRoot, "media-library", "library.sqlite3"),
    managedMediaRoot: path.join(app.getPath("videos"), "SubUtl Media"),
    managedWebMediaRoot: path.join(app.getPath("videos"), "SubUtl Web Media"),
    envFile: path.join(stateRoot, ".env"),
    glossaryFile: path.join(stateRoot, "glossary.txt"),
  };
}

function repoRoot(): string {
  return path.resolve(__dirname, "..", "..", "..");
}
