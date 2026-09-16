import { StringDecoder } from "node:string_decoder";
import { stripVTControlCharacters } from "node:util";

/** Decode before splitting: both UTF-8 characters and color codes can span chunks. */
export function logStream(onLine: (line: string) => void) {
  const decoder = new StringDecoder("utf8");
  let pending = "";
  const repeatedFoldingWarnings = new Map<string, number>();
  function emit(line: string) {
    const clean = stripVTControlCharacters(line);
    // Keep the first diagnostic for each session/node, and count exact repeats.
    // Other warnings and all errors always remain visible.
    const folding = clean.match(/\[W:onnxruntime:([^\]]*)\] (Could not find a CPU kernel and hence can't constant fold Mul node 'node_mul_164')\s*$/);
    if (folding) {
      const key = `${folding[1]}: ${folding[2]}`;
      const count = repeatedFoldingWarnings.get(key);
      repeatedFoldingWarnings.set(key, (count ?? 0) + 1);
      if (count !== undefined) return;
    }
    onLine(clean);
  }
  function drain() {
    let newline = pending.indexOf("\n");
    while (newline >= 0) {
      emit(pending.slice(0, newline + 1));
      pending = pending.slice(newline + 1);
      newline = pending.indexOf("\n");
    }
  }
  return {
    write(chunk: Buffer) {
      pending += decoder.write(chunk);
      drain();
    },
    end() {
      pending += decoder.end();
      drain();
      if (pending) emit(pending);
      pending = "";
      for (const [warning, count] of repeatedFoldingWarnings) {
        if (count > 1) onLine(`ONNX Runtime: ${count - 1} repeated warning(s): ${warning}\n`);
      }
      repeatedFoldingWarnings.clear();
    },
  };
}
