import { describe, expect, it } from "vitest";
import { logStream } from "./logStream";

describe("process log streams", () => {
  it("counts only the known repeated optimization warning, preserving other diagnostics", () => {
    const lines: string[] = [];
    const stream = logStream((line) => lines.push(line));
    const warning = "[W:onnxruntime:aligner pid=123, constant_folding.cc:278 ApplyImpl] Could not find a CPU kernel and hence can't constant fold Mul node 'node_mul_164'\n";
    stream.write(Buffer.from(`2026-09-16 15:17:25 ${warning}2026-09-16 15:17:26 ${warning}`));
    stream.write(Buffer.from("[E:onnxruntime] failed\n[W:onnxruntime] another warning\n"));
    stream.write(Buffer.from(warning.replace("pid=123", "pid=456")));
    stream.end();
    expect(lines).toHaveLength(5);
    expect(lines[0]).toContain("15:17:25");
    expect(lines[1]).toContain("failed");
    expect(lines[2]).toContain("another warning");
    expect(lines[3]).toContain("pid=456");
    expect(lines[4]).toContain("1 repeated warning(s)");
  });

  it("preserves Japanese text and removes color sequences split across byte chunks", () => {
    const lines: string[] = [];
    const stream = logStream((line) => lines.push(line));
    for (const byte of Buffer.from("\x1b[0;93m警告\x1b[m\nReady\n")) stream.write(Buffer.from([byte]));
    stream.end();
    expect(lines).toEqual(["警告\n", "Ready\n"]);
  });

  it("keeps concurrent streams separate and flushes an unterminated final error once", () => {
    const lines: string[] = [];
    const out = logStream((line) => lines.push(`out:${line}`));
    const err = logStream((line) => lines.push(`err:${line}`));
    out.write(Buffer.from("Load"));
    err.write(Buffer.from("\x1b[31mError: failed"));
    out.write(Buffer.from("ing\n"));
    err.end();
    err.end();
    out.end();
    expect(lines).toEqual(["out:Loading\n", "err:Error: failed"]);
  });
});
