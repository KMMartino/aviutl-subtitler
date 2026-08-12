import { ArrowLeft, ArrowRight, Check, Flag, Scissors, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { SilenceCutCandidate, SilenceCutDecision } from "../lib/types";
import { useI18n } from "../i18n";

type Props = {
  runId: string;
  reviewId: string;
  candidates: SilenceCutCandidate[];
  onSubmit(decisions: Array<{ candidateId: string; decision: SilenceCutDecision }>): Promise<void> | void;
  onCancel(): void;
};

export default function SilenceReviewScreen({ runId, reviewId, candidates, onSubmit, onCancel }: Props) {
  const { t } = useI18n();
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
  if (!candidate) return <main className="silence-review"><div className="loading">{t("silence.none")}</div></main>;

  function choose(decision: SilenceCutDecision) {
    setDecisions((current) => ({ ...current, [candidate.id]: decision }));
    if (index < candidates.length - 1) setIndex(index + 1);
  }
  function cancel() { if (window.confirm(t("review.cancelConfirm"))) onCancel(); }
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
    <header className="silence-review-header"><div><h1>{t("silence.title")}</h1><p>{t("silence.progress", { current: index + 1, total: candidates.length, decided, remaining: candidates.length - decided })}</p></div><button onClick={cancel}><X size={17} /> {t("review.cancelRun")}</button></header>
    <section className="silence-review-summary"><strong>{formatTime(candidate.cutStart)} – {formatTime(candidate.cutEnd)}</strong><span>{t("silence.proposedRemoval", { seconds: candidate.cutDuration.toFixed(2) })}</span><span>{t("silence.decision", { decision: decisionLabel(decisions[candidate.id], t) })}</span></section>
    <section className="silence-review-previews">
      <Preview title={t("silence.original")} description={t("silence.originalHelp")} videoRef={originalRef} url={originalUrl} onTime={track} onSeeking={seeking} onSeeked={seeked} onError={() => fallback ? setPreviewError(t("silence.originalPreviewError")) : setFallback(true)} />
      <Preview title={t("silence.after")} description={t("silence.afterHelp")} videoRef={seamRef} url={seamUrl} onTime={track} onSeeking={seeking} onSeeked={seeked} onError={() => fallback ? setPreviewError(t("silence.seamPreviewError")) : setFallback(true)} />
    </section>
    {fallback && !proxyUrls.original && !previewError && <div className="silence-preview-status">{t("silence.preparingCompatible")}</div>}
    {previewError && <div className="field-error" role="alert">{previewError}</div>}
    {submitError && <div className="field-error" role="alert">{submitError}</div>}
    <section className="silence-review-decisions">
      <button className={decisions[candidate.id] === "accept_cut" ? "active" : ""} onClick={() => choose("accept_cut")}><Scissors size={18} /> {t("silence.accept")} <kbd>A</kbd></button>
      <button className={decisions[candidate.id] === "reject_cut" ? "active" : ""} onClick={() => choose("reject_cut")}><Check size={18} /> {t("silence.reject")} <kbd>R</kbd></button>
      <button className={decisions[candidate.id] === "mark_and_reject" ? "active" : ""} onClick={() => choose("mark_and_reject")}><Flag size={18} /> {t("silence.markReject")} <kbd>M</kbd></button>
    </section>
    <footer className="silence-review-footer"><div className="silence-review-navigation"><button disabled={index === 0 || submitting} onClick={() => setIndex(index - 1)}><ArrowLeft size={17} /> {t("common.previous")}</button><div className="silence-review-dots">{candidates.map((item, itemIndex) => <button key={item.id} className={`${itemIndex === index ? "current" : ""} ${decisions[item.id] ? "decided" : ""}`} aria-label={t("silence.reviewSegment", { number: itemIndex + 1 })} onClick={() => setIndex(itemIndex)} />)}</div><button disabled={index === candidates.length - 1 || submitting} onClick={() => setIndex(index + 1)}>{t("common.next")} <ArrowRight size={17} /></button></div><button className="primary" disabled={!complete || submitting} onClick={() => void submit()}>{submitting ? t("common.submitting") : t("silence.submit")}</button></footer>
    <small className="silence-review-id">{t("silence.reviewId", { id: reviewId })}</small>
  </main>;
}

type PreviewProps = { title: string; description: string; url: string; videoRef: RefObject<HTMLVideoElement>; onTime(video: HTMLVideoElement): void; onSeeking(video: HTMLVideoElement): void; onSeeked(video: HTMLVideoElement): void; onError(): void };
const Preview = ({ title, description, url, videoRef, onTime, onSeeking, onSeeked, onError }: PreviewProps) => { const { t } = useI18n(); return <article className="silence-preview"><h2>{title}</h2><p>{description}</p>{url ? <video ref={videoRef} controls preload="metadata" src={url} onTimeUpdate={(event) => onTime(event.currentTarget)} onSeeking={(event) => onSeeking(event.currentTarget)} onSeeked={(event) => onSeeked(event.currentTarget)} onError={onError} /> : <div className="silence-preview-placeholder">{t("silence.preparingPreview")}</div>}</article>; };

function decisionLabel(decision: SilenceCutDecision | undefined, t: ReturnType<typeof useI18n>["t"]): string { return decision === "accept_cut" ? t("silence.accept") : decision === "reject_cut" ? t("silence.reject") : decision === "mark_and_reject" ? t("silence.markReject") : t("common.pending"); }
function formatTime(seconds: number): string { const whole = Math.max(0, Math.floor(seconds)); const hours = Math.floor(whole / 3600); const minutes = Math.floor((whole % 3600) / 60); const remainder = whole % 60; return `${hours ? `${hours}:` : ""}${minutes.toString().padStart(hours ? 2 : 1, "0")}:${remainder.toString().padStart(2, "0")}.${Math.floor((seconds % 1) * 10)}`; }
