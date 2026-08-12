import { describe, expect, it, vi } from "vitest";
import { assertTrustedSender, contentSecurityPolicy, installNavigationGuards, validateIpcArguments } from "./ipcSecurity";

const singleEditorialSource = (path: string, durationSeconds: number) => ({ path, durationSeconds, mode: "single", audioPath: path, visualPath: path, audioDurationSeconds: durationSeconds, visualDurationSeconds: durationSeconds, width: 1920, height: 1080, audioWidth: 1920, audioHeight: 1080, frameRate: 60, audioFrameRate: 60, pairingBasis: "single", roleConfirmed: true });

describe("IPC security boundary", () => {
  it("accepts only the configured renderer origin", () => {
    expect(() => assertTrustedSender({ senderFrame: { url: "http://127.0.0.1:5173/settings" } } as never, false)).not.toThrow();
    expect(() => assertTrustedSender({ senderFrame: { url: "https://attacker.invalid" } } as never, false)).toThrow(/untrusted/);
    expect(() => assertTrustedSender({ senderFrame: { url: "file:///app/index.html" } } as never, true, "file:///app/index.html")).not.toThrow();
    expect(() => assertTrustedSender({ senderFrame: { url: "file:///other/index.html" } } as never, true, "file:///app/index.html")).toThrow(/untrusted/);
    expect(() => assertTrustedSender({ senderFrame: { url: "https://attacker.invalid" } } as never, true)).toThrow(/untrusted/);
  });

  it("validates enums, paths, arity, and complete run requests", () => {
    expect(() => validateIpcArguments("llama:download", ["vulkan"])).not.toThrow();
    expect(() => validateIpcArguments("llama:download", ["metal"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("media:analyze", ["relative.mp4"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("dialog:input-file", ["C:\\media\\current.mp4"])).not.toThrow();
    expect(() => validateIpcArguments("dialog:input-file", ["relative.mp4"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("state:get", ["extra"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("run:start", [{
      workflow: "local", inputPath: "C:\\media\\in.mp4", outputPath: "C:\\media\\out.exo",
      configPath: "C:\\config\\local.json", envFile: "C:\\config\\.env", profile: false, sidecarsEnabled: false,
      cutSilenceEncoderPreset: "unconfigured", silencePreviewHeight: 360, silencePreviewFps: 8,
    }])).not.toThrow();
    expect(() => validateIpcArguments("run:start", [{ workflow: "local", inputPath: "C:\\in.mp4" }])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("editorial:inspect-checkpoint", ["C:\\media\\run-editorial.json", [singleEditorialSource("C:\\media\\one.mp4", 3600)]])).not.toThrow();
    expect(() => validateIpcArguments("editorial:inspect-checkpoint", ["relative.json"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("editorial:list-checkpoints", [])).not.toThrow();
    expect(() => validateIpcArguments("editorial:list-games", [])).not.toThrow();
    expect(() => validateIpcArguments("editorial:remember-game", ["Example Game"])).not.toThrow();
    expect(() => validateIpcArguments("editorial:remove-checkpoint", ["C:\\media\\run-editorial.json"])).not.toThrow();
    expect(() => validateIpcArguments("run:start", [{
      workflow: "hosted-long-stream", inputPath: "C:\\media\\one.mp4", outputPath: "C:\\media\\run.editorial.json",
      configPath: "C:\\config\\hosted-long-stream.json", envFile: "C:\\config\\.env", profile: true, sidecarsEnabled: true,
      cutSilenceEncoderPreset: "unconfigured", silencePreviewHeight: 360, silencePreviewFps: 8,
      editorialProject: {
        sources: [singleEditorialSource("C:\\media\\one.mp4", 3600)], titleOrGame: "Game", objective: "Finish",
        targetDurationMinSeconds: 1800, targetDurationMaxSeconds: 3000, mustKeepNotes: [], deEmphasizeNotes: [], outputLocale: "ja"
      }
    }])).not.toThrow();
    expect(() => validateIpcArguments("run:start", [{
      workflow: "local-long-stream", inputPath: "C:\\media\\one.mp4", outputPath: "C:\\media\\run.editorial.json",
      configPath: "C:\\config\\local-long-stream.json", envFile: "C:\\config\\.env", profile: true, sidecarsEnabled: true,
      cutSilenceEncoderPreset: "unconfigured", silencePreviewHeight: 360, silencePreviewFps: 8,
      editorialProject: {
        sources: [singleEditorialSource("C:\\media\\one.mp4", 3600)], titleOrGame: "Game", objective: "Finish",
        targetDurationMinSeconds: 1800, targetDurationMaxSeconds: 3000, mustKeepNotes: [], deEmphasizeNotes: []
      }
    }])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("silence:source", ["run-1"])).not.toThrow();
    expect(() => validateIpcArguments("silence:proxy", ["run-1", "silence-0001", "seam"])).not.toThrow();
    expect(() => validateIpcArguments("silence:proxy", ["run-1", "silence-0001", "file"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("broll:preview", ["run-1", "broll-0001"])).not.toThrow();
    expect(() => validateIpcArguments("run:submit-silence-review", ["run-1", "review-1", [{ candidateId: "silence-0001", decision: "accept_cut" }]])).not.toThrow();
    expect(() => validateIpcArguments("run:submit-silence-review", ["run-1", "review-1", [{ candidateId: "silence-0001", decision: "maybe" }]])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("library:add-root", ["C:\\media\\broll"])).not.toThrow();
    expect(() => validateIpcArguments("library:add-root", ["..\\broll"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("library:list-assets", [{ query: "boss fight", limit: 50 }])).not.toThrow();
    expect(() => validateIpcArguments("library:list-assets", [{ query: "boss fight", limit: 500 }])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("library:list-assets", [{ sql: "DROP TABLE assets" }])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("library:update-description", ["asset-1", "Gameplay in a ruined arena"])).not.toThrow();
    expect(() => validateIpcArguments("library:remove-root", ["root-1"])).not.toThrow();
    expect(() => validateIpcArguments("library:thumbnails", [["asset-1", "asset-2"]])).not.toThrow();
    expect(() => validateIpcArguments("library:analysis-estimates", ["asset-1"])).not.toThrow();
    expect(() => validateIpcArguments("library:analysis-estimates", ["asset-1", { startMs: 25_000, endMs: 100_000 }])).not.toThrow();
    expect(() => validateIpcArguments("library:set-directory-hidden", ["root-1", "frames", true])).not.toThrow();
    expect(() => validateIpcArguments("library:analyze", ["asset-1", "detailed"])).not.toThrow();
    expect(() => validateIpcArguments("library:analyze", ["asset-1", "precise"])).not.toThrow();
    expect(() => validateIpcArguments("library:analyze", ["asset-1", "probe"])).not.toThrow();
    expect(() => validateIpcArguments("library:analyze", ["asset-1", "detailed", { startMs: 25_000, endMs: 100_000 }])).not.toThrow();
    expect(() => validateIpcArguments("library:add-segment", ["asset-1", { startMs: 25_000, endMs: 100_000 }, "Combat trailer"])).not.toThrow();
    expect(() => validateIpcArguments("library:add-segment", ["asset-1", { startMs: 100_000, endMs: 25_000 }, "Bad range"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("run:submit-broll-review", ["run-1", "review-1", [{ candidateId: "candidate-1", decision: "use_library" }]])).not.toThrow();
    expect(() => validateIpcArguments("library:analyze", ["asset-1", "extreme"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("library:bulk-analysis-plan", ["video"])).not.toThrow();
  });

  it("denies new windows and all top-level navigation", () => {
    let navigation: ((event: { preventDefault(): void }, url: string) => void) | undefined;
    const setWindowOpenHandler = vi.fn();
    installNavigationGuards({ webContents: { setWindowOpenHandler, on: (_name: string, callback: typeof navigation) => { navigation = callback; } } });
    expect(setWindowOpenHandler.mock.calls[0][0]()).toEqual({ action: "deny" });
    const preventDefault = vi.fn();
    navigation?.({ preventDefault }, "https://attacker.invalid");
    expect(preventDefault).toHaveBeenCalled();
  });

  it("uses a strict packaged CSP while permitting the Vite development preamble", () => {
    expect(contentSecurityPolicy(true)).toContain("script-src 'self';");
    expect(contentSecurityPolicy(true)).not.toContain("script-src 'self' 'unsafe-inline'");
    expect(contentSecurityPolicy(false)).toContain("script-src 'self' 'unsafe-inline'");
    expect(contentSecurityPolicy(true)).toContain("connect-src 'self';");
    expect(contentSecurityPolicy(true)).not.toContain("http://127.0.0.1");
    expect(contentSecurityPolicy(true)).not.toContain("ws://127.0.0.1");
    expect(contentSecurityPolicy(false)).toContain("connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*");
    expect(contentSecurityPolicy(true)).toContain("object-src 'none'");
    expect(contentSecurityPolicy(true)).toContain("media-src 'self' subutl-media: blob:");
  });
});
