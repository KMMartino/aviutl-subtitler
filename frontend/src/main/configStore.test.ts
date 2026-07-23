import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { ensureFrontendState, importGlossary, loadAppState, readGlossary, resetFrontendState, saveActiveAlignmentModel, saveAppSettings, saveWorkflowConfig, withAlignmentModel } from "./configStore";
import type { RuntimePaths } from "./paths";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("config store runtime paths", () => {
  it("updates alignment selection without discarding unrelated workflow settings", () => {
    expect(withAlignmentModel({ backend: { name: "existing" }, alignment: { language: "ja" } }, "C:/managed/alignment", true)).toEqual({
      backend: { name: "existing" },
      alignment: { language: "ja", model: "C:/managed/alignment", offline_model_cache: true },
    });
  });

  it("immediately saves alignment selection to the active workflow", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    saveActiveAlignmentModel("C:/managed/alignment", true, paths);

    const state = loadAppState(paths);
    expect(state.configs.local.alignment).toEqual({
      model: "C:/managed/alignment",
      offline_model_cache: true,
    });
    expect(state.configs.hosted.alignment).toEqual({
      model: "C:/managed/alignment",
      offline_model_cache: true,
    });
    expect(state.settings.alignmentModel).toBe("C:/managed/alignment");
  });

  it("copies bundled config templates into writable user config state", () => {
    const paths = makePaths();
    fs.mkdirSync(paths.bundledConfigRoot, { recursive: true });
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "local.json"), JSON.stringify({ backend: { name: "existing-pipeline" } }));
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "hosted.json"), JSON.stringify({ hosted: true }));
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "local-long-stream.json"), JSON.stringify({ long: "local" }));
    fs.writeFileSync(path.join(paths.bundledConfigRoot, "hosted-long-stream.json"), JSON.stringify({ long: "hosted" }));

    ensureFrontendState(paths);

    expect(fs.existsSync(path.join(paths.userConfigRoot, "local.json"))).toBe(true);
    expect(fs.existsSync(path.join(paths.stateRoot, "settings.json"))).toBe(true);
    expect(fs.existsSync(paths.envFile)).toBe(true);
    const settings = loadAppState(paths).settings;
    expect(settings.cutSilenceEncoderPreset).toBe("unconfigured");
    expect(settings.silencePreviewHeight).toBe(360);
    expect(settings.silencePreviewFps).toBe(8);
  });

  it("does not overwrite existing user workflow configs", () => {
    const paths = makePaths();
    fs.mkdirSync(paths.bundledConfigRoot, { recursive: true });
    for (const workflow of ["local", "hosted", "local-long-stream", "hosted-long-stream"]) {
      fs.writeFileSync(path.join(paths.bundledConfigRoot, `${workflow}.json`), JSON.stringify({ template: workflow }));
    }
    ensureFrontendState(paths);
    saveWorkflowConfig("hosted", { user: "edited" }, paths);
    ensureFrontendState(paths);

    expect(loadAppState(paths).configs.hosted.user).toBe("edited");
  });

  it("recovers a truncated primary settings file from its backup", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    const first = loadAppState(paths).settings;
    saveAppSettings({ ...first, theme: "forest" }, paths);
    fs.writeFileSync(path.join(paths.stateRoot, "settings.json"), "{", "utf8");

    expect(loadAppState(paths).settings.theme).toBe(first.theme);
    expect(() => JSON.parse(fs.readFileSync(path.join(paths.stateRoot, "settings.json"), "utf8"))).not.toThrow();
    fs.writeFileSync(path.join(paths.stateRoot, "settings.json"), "{", "utf8");
    expect(loadAppState(paths).settings.theme).toBe(first.theme);
  });

  it("reports an actionable error when primary and backup state are corrupt", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    fs.writeFileSync(path.join(paths.stateRoot, "settings.json"), "{", "utf8");
    fs.writeFileSync(path.join(paths.stateRoot, "settings.json.bak"), "{", "utf8");
    expect(() => loadAppState(paths)).toThrow(/Reset or restore/);
  });

  it("resets invalid persisted state while retaining a recovery copy", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    fs.writeFileSync(path.join(paths.stateRoot, "settings.json"), "{", "utf8");
    const state = resetFrontendState(paths);
    expect(state.settings.selectedWorkflow).toBe("local");
    expect(fs.readdirSync(paths.stateRoot).some((name) => name.startsWith("settings.json.invalid-"))).toBe(true);
  });

  it("migrates one legacy alignment choice to every workflow while preserving options", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    const settingsFile = path.join(paths.stateRoot, "settings.json");
    const settings = JSON.parse(fs.readFileSync(settingsFile, "utf8"));
    delete settings.alignmentModel;
    delete settings.alignmentOfflineModelCache;
    fs.writeFileSync(settingsFile, JSON.stringify(settings), "utf8");
    saveWorkflowConfig("local", { alignment: { model: "MahmoudAshraf/mms-300m-1130-forced-aligner", offline_model_cache: false } }, paths);
    saveWorkflowConfig("hosted", { alignment: { model: "C:/legacy", offline_model_cache: true, language: "ja", split_size: "char" } }, paths);

    const state = loadAppState(paths);
    expect(state.settings.alignmentModel).toBe("C:/legacy");
    expect(state.configs.local.alignment?.model).toBe("C:/legacy");
    expect(state.configs.hosted.alignment?.language).toBe("ja");
    expect(state.configs.hosted.alignment?.split_size).toBe("char");
  });

  it("migrates the old hosted fallback default to Gemini Pro", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    saveWorkflowConfig("hosted", {
      backend: {
        transcriber: "gemini",
        transcription_model: "gemini-3.5-flash",
        fallback_transcriber: "openai",
        fallback_transcription_model: "gpt-4o-mini-transcribe"
      },
      cleanup: {
        backend: "openai",
        api_model: "gpt-5.4-mini",
        window_subtitles: 8,
        skip_final_review: true
      }
    }, paths);

    const state = loadAppState(paths);

    expect(state.configs.hosted.backend.fallback_transcriber).toBe("gemini");
    expect(state.configs.hosted.backend.fallback_transcription_model).toBe("gemini-3.1-pro-preview");
    expect(state.configs.hosted.cleanup.skip_final_review).toBe(false);
  });

  it("keeps a user-selected fallback while migrating an obsolete cleanup model", () => {
    const paths = makePaths();
    writeWorkflowTemplates(paths);
    ensureFrontendState(paths);
    saveWorkflowConfig("hosted", {
      backend: {
        transcriber: "gemini",
        transcription_model: "gemini-3.1-pro-preview",
        fallback_transcriber: "openai",
        fallback_transcription_model: "gpt-4o-mini-transcribe"
      },
      cleanup: {
        backend: "gemini",
        api_model: "gemini-3.1-pro-preview",
        skip_final_review: true
      }
    }, paths);

    const state = loadAppState(paths);

    expect(state.configs.hosted.backend.fallback_transcriber).toBe("openai");
    expect(state.configs.hosted.backend.fallback_transcription_model).toBe("gpt-4o-mini-transcribe");
    expect(state.configs.hosted.cleanup.backend).toBe("gemini");
    expect(state.configs.hosted.cleanup.api_model).toBe("gemini-3.5-flash");
    expect(state.configs.hosted.cleanup.thinking_level).toBe("minimal");
    expect(state.configs.hosted.cleanup.skip_final_review).toBe(true);
  });

  it("imports a glossary by copying it into managed user state", () => {
    const paths = makePaths();
    const source = path.join(paths.appResourceRoot, "external-glossary.txt");
    fs.mkdirSync(path.dirname(source), { recursive: true });
    fs.writeFileSync(source, "PSSR | PlayStation image upscaling\n", "utf8");

    const imported = importGlossary(source, paths);
    fs.writeFileSync(source, "changed\n", "utf8");

    expect(imported).toBe("PSSR | PlayStation image upscaling\n");
    expect(readGlossary(paths)).toBe("PSSR | PlayStation image upscaling\n");
  });

  it("migrates SubUtl state from the old packaged user-data folder", () => {
    const paths = makeSubUtlPaths();
    writeWorkflowTemplates(paths);
    // Electron pre-creates Chromium-internal entries before application state.
    fs.mkdirSync(path.join(paths.stateRoot, "Cache"), { recursive: true });
    fs.writeFileSync(path.join(paths.stateRoot, "Cache", "index"), "internal", "utf8");
    const legacyRoot = path.join(path.dirname(paths.stateRoot), "subtitler-frontend");
    fs.mkdirSync(path.join(legacyRoot, "configs"), { recursive: true });
    fs.writeFileSync(path.join(legacyRoot, ".env"), "", "utf8");
    fs.writeFileSync(path.join(legacyRoot, "settings.json"), JSON.stringify({
      pythonPath: path.join(legacyRoot, "python", "Scripts", "python.exe"),
      modelsDirectory: path.join(legacyRoot, "models"),
      selectedWorkflow: "local",
    }, null, 2));
    for (const workflow of ["local", "hosted", "local-long-stream", "hosted-long-stream"]) {
      fs.writeFileSync(path.join(legacyRoot, "configs", `${workflow}.json`), JSON.stringify({
        local: { model: path.join(legacyRoot, "models", "model.gguf") }
      }, null, 2));
    }

    const state = loadAppState(paths);

    expect(fs.existsSync(legacyRoot)).toBe(false);
    expect(fs.existsSync(paths.stateRoot)).toBe(true);
    expect(state.settings.pythonPath).toContain(paths.stateRoot);
    expect(state.configs.local.local.model).toContain(paths.stateRoot);
    expect(state.configs.local.local.model).not.toContain("subtitler-frontend");
    expect(fs.readFileSync(path.join(paths.stateRoot, "Cache", "index"), "utf8")).toBe("internal");
  });

  it("does not overwrite SubUtl state when an application marker exists", () => {
    const paths = makeSubUtlPaths();
    writeWorkflowTemplates(paths);
    const legacyRoot = path.join(path.dirname(paths.stateRoot), "subtitler-frontend");
    fs.mkdirSync(legacyRoot, { recursive: true });
    fs.writeFileSync(path.join(legacyRoot, "settings.json"), JSON.stringify({ theme: "forest" }));
    fs.mkdirSync(paths.stateRoot, { recursive: true });
    fs.writeFileSync(path.join(paths.stateRoot, "settings.json"), JSON.stringify({ theme: "graphite" }));

    const state = loadAppState(paths);

    expect(fs.existsSync(legacyRoot)).toBe(true);
    expect(state.settings.theme).toBe("graphite");
  });

  it("preserves both roots without a partial merge when an internal entry collides", () => {
    const paths = makeSubUtlPaths();
    writeWorkflowTemplates(paths);
    const legacyRoot = path.join(path.dirname(paths.stateRoot), "subtitler-frontend");
    fs.mkdirSync(path.join(legacyRoot, "Cache"), { recursive: true });
    fs.writeFileSync(path.join(legacyRoot, "Cache", "legacy"), "legacy", "utf8");
    fs.writeFileSync(path.join(legacyRoot, "settings.json"), JSON.stringify({ theme: "forest" }));
    fs.mkdirSync(path.join(paths.stateRoot, "Cache"), { recursive: true });
    fs.writeFileSync(path.join(paths.stateRoot, "Cache", "current"), "current", "utf8");

    expect(() => loadAppState(paths)).toThrow("destination entries already exist: Cache");
    expect(fs.existsSync(path.join(legacyRoot, "settings.json"))).toBe(true);
    expect(fs.existsSync(path.join(legacyRoot, "Cache", "legacy"))).toBe(true);
    expect(fs.existsSync(path.join(paths.stateRoot, "Cache", "current"))).toBe(true);
    expect(fs.existsSync(path.join(paths.stateRoot, "settings.json"))).toBe(false);
  });
});

function makePaths(): RuntimePaths {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-config-"));
  roots.push(root);
  return {
    appResourceRoot: path.join(root, "resources"),
    bundledBackendRoot: path.join(root, "resources", "app-backend"),
    bundledConfigRoot: path.join(root, "resources", "app-backend", "configs"),
    userDataRoot: path.join(root, "userData"),
    stateRoot: path.join(root, "userData"),
    userConfigRoot: path.join(root, "userData", "configs"),
    userToolsRoot: path.join(root, "userData", "tools"),
    userModelsRoot: path.join(root, "userData", "models"),
    managedPythonRoot: path.join(root, "userData", "python"),
    managedFfmpegRoot: path.join(root, "userData", "tools", "ffmpeg"),
    envFile: path.join(root, "userData", ".env"),
    glossaryFile: path.join(root, "userData", "glossary.txt"),
  };
}

function makeSubUtlPaths(): RuntimePaths {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "subtitler-config-"));
  roots.push(root);
  return {
    appResourceRoot: path.join(root, "resources"),
    bundledBackendRoot: path.join(root, "resources", "app-backend"),
    bundledConfigRoot: path.join(root, "resources", "app-backend", "configs"),
    userDataRoot: path.join(root, "SubUtl"),
    stateRoot: path.join(root, "SubUtl"),
    userConfigRoot: path.join(root, "SubUtl", "configs"),
    userToolsRoot: path.join(root, "SubUtl", "tools"),
    userModelsRoot: path.join(root, "SubUtl", "models"),
    managedPythonRoot: path.join(root, "SubUtl", "python"),
    managedFfmpegRoot: path.join(root, "SubUtl", "tools", "ffmpeg"),
    envFile: path.join(root, "SubUtl", ".env"),
    glossaryFile: path.join(root, "SubUtl", "glossary.txt"),
  };
}

function writeWorkflowTemplates(paths: RuntimePaths): void {
  fs.mkdirSync(paths.bundledConfigRoot, { recursive: true });
  for (const workflow of ["local", "hosted", "local-long-stream", "hosted-long-stream"]) {
    fs.writeFileSync(path.join(paths.bundledConfigRoot, `${workflow}.json`), JSON.stringify({ template: workflow }));
  }
}
