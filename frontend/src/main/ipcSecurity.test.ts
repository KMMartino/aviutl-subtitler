import { describe, expect, it, vi } from "vitest";
import { assertTrustedSender, contentSecurityPolicy, installNavigationGuards, validateIpcArguments } from "./ipcSecurity";

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
    expect(() => validateIpcArguments("silence:source", ["run-1"])).not.toThrow();
    expect(() => validateIpcArguments("silence:proxy", ["run-1", "silence-0001", "seam"])).not.toThrow();
    expect(() => validateIpcArguments("silence:proxy", ["run-1", "silence-0001", "file"])).toThrow(/Invalid IPC/);
    expect(() => validateIpcArguments("run:submit-silence-review", ["run-1", "review-1", [{ candidateId: "silence-0001", decision: "accept_cut" }]])).not.toThrow();
    expect(() => validateIpcArguments("run:submit-silence-review", ["run-1", "review-1", [{ candidateId: "silence-0001", decision: "maybe" }]])).toThrow(/Invalid IPC/);
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
