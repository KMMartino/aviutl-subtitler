import { ArrowLeft, ArrowRight, Check, Flag, Scissors, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { SilenceCutCandidate, SilenceCutDecision } from "../lib/types";

type Props = {
  runId: string;
  reviewId: string;
  candidates: SilenceCutCandidate[];
  onSubmit(decisions: Array<{ candidateId: string; decision: SilenceCutDecision }>): Promise<void> | void;
  onCancel(): void;
};

export default function SilenceReviewScreen({ runId, reviewId, candidates, onSubmit, onCancel }: Props) {
  const [index, setIndex] = useState(0);
  const [decisions, setDecisions] = useState<Record<string, SilenceCutDecision>>({});
  const [sourceUrl, setSourceUrl] = useState("");
  const [fallback, setFallback] = useState(false);
  const [proxyUrls, setProxyUrls] = useState<{ original: string; seam: string }>({ original: "", seam: "" });
  const [previewError, setPreviewError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const originalRef = useRef<HTMLVideoElement>(null);
  const seamRef = useRef<HTMLVideoElement>(null);
  const candidate = candidates[index];
  const decided = Object.keys(decisions).length;
  const complete = decided === candidates.length;

  useEffect(() => { void window.subtitler.getSilenceSource(runId).then((result) => setSourceUrl(result.url)).catch((error) => setPreviewError(String(error))); }, [runId]);
  useEffect(() => {
    setProxyUrls({ original: "", seam: "" });
    setPreviewError("");
    if (!fallback || !candidate) return;
    void Promise.all([
      window.subtitler.getSilenceProxy(runId, candidate.id, "original"),
      window.subtitler.getSilenceProxy(runId, candidate.id, "seam"),
    ]).then(([original, seam]) => {
      setProxyUrls({ original: original.url, seam: seam.url });
      const next = candidates[index + 1];
      if (next) void window.subtitler.prefetchSilenceProxies(runId, [next.id]);
    }).catch((error) => setPreviewError(error instanceof Error ? error.message : String(error)));
  }, [candidate?.id, fallback, index, runId]);

  useEffect(() => {
    if (!candidate) return;
    const videos = [originalRef.current, seamRef.current].filter((video): video is HTMLVideoElement => Boolean(video));
    const start = fallback ? 0 : Math.max(0, candidate.cutStart - 2);
    const seek = (video: HTMLVideoElement) => {
      const maximum = Number.isFinite(video.duration) ? Math.max(0, video.duration - 0.01) : start;
      video.currentTime = Math.min(start, maximum);
    };
    const pending: Array<[HTMLVideoElement, () => void]> = [];
    for (const video of videos) {
      video.pause();
      video.dataset.seam = video === seamRef.current ? "true" : "false";
      if (video.readyState >= HTMLMediaElement.HAVE_METADATA) seek(video);
      else {
        const listener = () => seek(video);
        video.addEventListener("loadedmetadata", listener, { once: true });
        pending.push([video, listener]);
      }
    }
    return () => {
      for (const [video, listener] of pending) video.removeEventListener("loadedmetadata", listener);
    };
  }, [candidate?.id, fallback, proxyUrls.original, proxyUrls.seam, sourceUrl]);

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      const tagName = event.target instanceof HTMLElement ? event.target.tagName : "";
      if (["VIDEO", "INPUT", "SELECT", "TEXTAREA"].includes(tagName)) return;
      if (event.key.toLowerCase() === "a") choose("accept_cut");
      else if (event.key.toLowerCase() === "r") choose("reject_cut");
      else if (event.key.toLowerCase() === "m") choose("mark_and_reject");
      else if (event.key === "ArrowLeft") setIndex((value) => Math.max(0, value - 1));
      else if (event.key === "ArrowRight") setIndex((value) => Math.min(candidates.length - 1, value + 1));
      else if (event.key === " ") {
        event.preventDefault();
        const video = seamRef.current;
        if (video) void (video.paused ? video.play() : Promise.resolve(video.pause()));
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [candidate?.id, candidates.length]);

  const rows = useMemo(() => candidates.map((item) => ({ candidateId: item.id, decision: decisions[item.id] })).filter((item): item is { candidateId: string; decision: SilenceCutDecision } => Boolean(item.decision)), [candidates, decisions]);
  if (!candidate) return <main className="silence-review"><div className="loading">No silence cuts require review.</div></main>;

  function choose(decision: SilenceCutDecision) {
    setDecisions((current) => ({ ...current, [candidate.id]: decision }));
    if (index < candidates.length - 1) setIndex(index + 1);
  }
  function cancel() { if (window.confirm("Cancel this entire subtitle run? No output will be written.")) onCancel(); }
  async function submit() {
    setSubmitting(true); setSubmitError("");
    try { await onSubmit(rows); }
    catch (error) { setSubmitError(error instanceof Error ? error.message : String(error)); setSubmitting(false); }
  }
  function track(video: HTMLVideoElement) {
    if (fallback) return;
    const graceUntil = Number(video.dataset.seekGraceUntil ?? 0);
    if (video.seeking || video.dataset.userSeeking === "true" || Date.now() < graceUntil) return;
    if (!video.paused && video.dataset.seam === "true" && video.currentTime >= candidate.cutStart && video.currentTime < candidate.cutEnd) video.currentTime = candidate.cutEnd;
  }
  function seeking(video: HTMLVideoElement) { video.dataset.userSeeking = "true"; }
  function seeked(video: HTMLVideoElement) {
    video.dataset.userSeeking = "false";
    video.dataset.seekGraceUntil = String(Date.now() + 750);
  }
  const originalUrl = fallback ? proxyUrls.original : sourceUrl;
  const seamUrl = fallback ? proxyUrls.seam : sourceUrl;
  return <main className="silence-review">
    <header className="silence-review-header"><div><h1>Review silence cut</h1><p>Segment {index + 1} of {candidates.length} · {decided} decided · {candidates.length - decided} remaining</p></div><button onClick={cancel}><X size={17} /> Cancel run</button></header>
    <section className="silence-review-summary"><strong>{formatTime(candidate.cutStart)} – {formatTime(candidate.cutEnd)}</strong><span>Proposed removal: {candidate.cutDuration.toFixed(2)} seconds</span><span>Decision: {decisionLabel(decisions[candidate.id])}</span></section>
    <section className="silence-review-previews">
      <Preview title="Original neighborhood" description="Starts two seconds before the proposed cut and remains fully scrubbable." videoRef={originalRef} url={originalUrl} onTime={track} onSeeking={seeking} onSeeked={seeked} onError={() => fallback ? setPreviewError("The compatible original preview could not be played.") : setFallback(true)} />
      <Preview title="After cut" description="Starts two seconds before and simulates the resulting visual and audio seam during playback." videoRef={seamRef} url={seamUrl} onTime={track} onSeeking={seeking} onSeeked={seeked} onError={() => fallback ? setPreviewError("The compatible seam preview could not be played.") : setFallback(true)} />
    </section>
    {fallback && !proxyUrls.original && !previewError && <div className="silence-preview-status">Preparing compatible preview…</div>}
    {previewError && <div className="field-error" role="alert">{previewError}</div>}
    {submitError && <div className="field-error" role="alert">{submitError}</div>}
    <section className="silence-review-decisions">
      <button className={decisions[candidate.id] === "accept_cut" ? "active" : ""} onClick={() => choose("accept_cut")}><Scissors size={18} /> Accept cut <kbd>A</kbd></button>
      <button className={decisions[candidate.id] === "reject_cut" ? "active" : ""} onClick={() => choose("reject_cut")}><Check size={18} /> Reject cut <kbd>R</kbd></button>
      <button className={decisions[candidate.id] === "mark_and_reject" ? "active" : ""} onClick={() => choose("mark_and_reject")}><Flag size={18} /> Mark and reject <kbd>M</kbd></button>
    </section>
    <footer className="silence-review-footer"><div className="silence-review-navigation"><button disabled={index === 0 || submitting} onClick={() => setIndex(index - 1)}><ArrowLeft size={17} /> Previous</button><div className="silence-review-dots">{candidates.map((item, itemIndex) => <button key={item.id} className={`${itemIndex === index ? "current" : ""} ${decisions[item.id] ? "decided" : ""}`} aria-label={`Review segment ${itemIndex + 1}`} onClick={() => setIndex(itemIndex)} />)}</div><button disabled={index === candidates.length - 1 || submitting} onClick={() => setIndex(index + 1)}>Next <ArrowRight size={17} /></button></div><button className="primary" disabled={!complete || submitting} onClick={() => void submit()}>{submitting ? "Submitting…" : "Submit decisions"}</button></footer>
    <small className="silence-review-id">Review {reviewId}</small>
  </main>;
}

type PreviewProps = { title: string; description: string; url: string; videoRef: RefObject<HTMLVideoElement>; onTime(video: HTMLVideoElement): void; onSeeking(video: HTMLVideoElement): void; onSeeked(video: HTMLVideoElement): void; onError(): void };
const Preview = ({ title, description, url, videoRef, onTime, onSeeking, onSeeked, onError }: PreviewProps) => <article className="silence-preview"><h2>{title}</h2><p>{description}</p>{url ? <video ref={videoRef} controls preload="metadata" src={url} onTimeUpdate={(event) => onTime(event.currentTarget)} onSeeking={(event) => onSeeking(event.currentTarget)} onSeeked={(event) => onSeeked(event.currentTarget)} onError={onError} /> : <div className="silence-preview-placeholder">Preparing preview…</div>}</article>;

function decisionLabel(decision?: SilenceCutDecision): string { return decision === "accept_cut" ? "Accept cut" : decision === "reject_cut" ? "Reject cut" : decision === "mark_and_reject" ? "Mark and reject" : "Pending"; }
function formatTime(seconds: number): string { const whole = Math.max(0, Math.floor(seconds)); const hours = Math.floor(whole / 3600); const minutes = Math.floor((whole % 3600) / 60); const remainder = whole % 60; return `${hours ? `${hours}:` : ""}${minutes.toString().padStart(hours ? 2 : 1, "0")}:${remainder.toString().padStart(2, "0")}.${Math.floor((seconds % 1) * 10)}`; }
