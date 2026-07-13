import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import type { CoreWorkflowSettings, MediaAnalysis } from "../lib/types";

export function useMediaAnalysis(inputPath: string, requestRevision: number, setCoreSettings: Dispatch<SetStateAction<CoreWorkflowSettings | null>>) {
  const [analysis, setAnalysis] = useState<MediaAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState("");

  useEffect(() => {
    if (!inputPath) {
      setAnalysis(null);
      setAnalysisError("");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setAnalyzing(true);
      setAnalysisError("");
      try {
        const result = await window.subtitler.analyzeMedia(inputPath);
        if (cancelled) return;
        setAnalysis(result);
        setCoreSettings((current) => {
          if (!current || !result.audioTracks.length || result.audioTracks.some((track) => track.audioIndex === current.audioTrack)) return current;
          return { ...current, audioTrack: result.audioTracks[0].audioIndex };
        });
      } catch (error) {
        if (cancelled) return;
        setAnalysis(null);
        setAnalysisError(error instanceof Error ? error.message : String(error));
      } finally {
        if (!cancelled) setAnalyzing(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [inputPath, requestRevision, setCoreSettings]);

  const clearAnalysis = useCallback(() => {
    setAnalysis(null);
    setAnalysisError("");
  }, []);
  return { analysis, analyzing, analysisError, clearAnalysis };
}
