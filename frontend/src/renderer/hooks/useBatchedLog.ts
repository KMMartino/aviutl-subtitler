import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_LOG_LIMIT, LogBuffer } from "../lib/logBuffer";

const FLUSH_INTERVAL_MS = 50;

export function useBatchedLog(limit = DEFAULT_LOG_LIMIT) {
  const buffer = useRef(new LogBuffer(limit));
  const timer = useRef<number | null>(null);
  const [logs, setLogs] = useState("");

  const flush = useCallback(() => {
    timer.current = null;
    setLogs(buffer.current.value());
  }, []);

  const schedule = useCallback(() => {
    if (timer.current === null) timer.current = window.setTimeout(flush, FLUSH_INTERVAL_MS);
  }, [flush]);

  const append = useCallback((text: string) => {
    buffer.current.append(text);
    schedule();
  }, [schedule]);

  const replace = useCallback((text: string) => {
    buffer.current.replace(text);
    if (timer.current !== null) window.clearTimeout(timer.current);
    flush();
  }, [flush]);

  const clear = useCallback(() => replace(""), [replace]);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  return { logs, append, replace, clear };
}
