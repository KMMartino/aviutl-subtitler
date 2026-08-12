import { ArrowLeft, ArrowRight, Check, Eye, FolderOpen, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { BrollCandidate, BrollReviewDecision, MediaAnalysisDetail, MediaAnalysisScope, MediaAssetAnalysisEstimate, MediaAssetDetail } from "../lib/types";
import { formatTimecode, parseTimecode } from "../lib/timecodes";
import { useI18n } from "../i18n";

type Props = {
  runId: string;
  reviewId: string;
  candidates: BrollCandidate[];
  onSubmit(decisions: BrollReviewDecision[]): Promise<void> | void;
  onCancel(): Promise<void> | void;
};

type AnalysisTarget = { scope?: MediaAnalysisScope; estimates: MediaAssetAnalysisEstimate[] };

export default function BrollReviewScreen({ runId, reviewId, candidates, onSubmit, onCancel }: Props) {
  const { t } = useI18n();
  const [index, setIndex] = useState(0);
  const [decisions, setDecisions] = useState<Record<string, BrollReviewDecision>>({});
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<{ url: string; mediaKind: "video" | "image" } | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [asset, setAsset] = useState<MediaAssetDetail | null>(null);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [rangeStart, setRangeStart] = useState("0:00");
  const [rangeEnd, setRangeEnd] = useState("");
  const [rangeDescription, setRangeDescription] = useState("");
  const [analysisTarget, setAnalysisTarget] = useState<AnalysisTarget | null>(null);
  const [analysisDetail, setAnalysisDetail] = useState<MediaAnalysisDetail>("simple");
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [segmentBusy, setSegmentBusy] = useState(false);
  const candidate = candidates[index];
  const description = candidate ? descriptions[candidate.id] ?? "" : "";
  const rows = useMemo(() => candidates.flatMap((item) => {
    const decision = decisions[item.id];
    if (!decision) return [];
    if (decision.decision === "describe" && !decision.description?.trim()) return [];
    return [decision];
  }), [candidates, decisions]);

  useEffect(() => {
    if (!candidate) return;
    let current = true;
    setPreview(null);
    setPreviewError("");
    setAsset(null);
    setRangeOpen(false);
    setRangeStart("0:00");
    setRangeEnd(candidate.sourceEndSec ? formatTimecode(candidate.sourceEndSec * 1000) : "");
    setRangeDescription("");
    setAnalysisTarget(null);
    setNotice("");
    void Promise.all([
      window.subtitler.getBrollPreview(runId, candidate.id),
      window.subtitler.getMediaAsset(candidate.assetId),
    ]).then(
      ([nextPreview, nextAsset]) => {
        if (!current) return;
        setPreview(nextPreview);
        setAsset(nextAsset);
        if (nextAsset.durationMs) setRangeEnd(formatTimecode(nextAsset.durationMs));
      },
      (reason: unknown) => {
        if (current) setPreviewError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => { current = false; };
  }, [candidate, runId]);

  if (!candidate) return <main className="broll-review"><div className="loading">{t("broll.none")}</div></main>;

  function decide(decision: BrollReviewDecision) {
    setError("");
    setDecisions((current) => ({ ...current, [candidate.id]: decision }));
    if (index < candidates.length - 1) setIndex(index + 1);
  }

  function describe() {
    const text = description.trim();
    if (!text) {
      setError(t("broll.describeFileError"));
      return;
    }
    decide({ candidateId: candidate.id, decision: "describe", description: text });
  }

  function reject() {
    decide({ candidateId: candidate.id, decision: "reject" });
  }

  function parsedScope(): MediaAnalysisScope {
    const durationMs = asset?.durationMs ?? Math.round((candidate.sourceEndSec ?? 0) * 1000);
    const scope = {
      startMs: parseTimecode(rangeStart, 0),
      endMs: parseTimecode(rangeEnd, durationMs),
    };
    if (scope.endMs <= scope.startMs) throw new Error(t("broll.rangeOrderError"));
    if (durationMs > 0 && scope.endMs > durationMs) throw new Error(t("broll.rangeInsideError"));
    return scope;
  }

  async function openAnalysis(scope?: MediaAnalysisScope) {
    setError("");
    setNotice("");
    try {
      const estimates = await window.subtitler.estimateMediaAssetAnalysis(candidate.assetId, scope);
      const recommended = estimates.find((estimate) => estimate.recommended) ?? estimates[0];
      setAnalysisDetail(recommended?.detail ?? "simple");
      setAnalysisTarget({ scope, estimates });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function analyze() {
    if (!analysisTarget) return;
    setAnalysisBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await window.subtitler.analyzeMediaAsset(
        candidate.assetId,
        analysisDetail,
        analysisTarget.scope,
      );
      setAsset(result.asset);
      setAnalysisTarget(null);
      setNotice(
        analysisTarget.scope
          ? t("broll.rangeAnalyzed", { count: result.asset.segments.filter((segment) => segment.endMs > analysisTarget.scope!.startMs && segment.startMs < analysisTarget.scope!.endMs).length })
          : t("broll.analysisSaved"),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAnalysisBusy(false);
    }
  }

  async function saveSegment() {
    setSegmentBusy(true);
    setError("");
    setNotice("");
    try {
      const text = rangeDescription.trim();
      if (!text) throw new Error(t("broll.describeRangeError"));
      const updated = await window.subtitler.addMediaAssetSegment(candidate.assetId, parsedScope(), text);
      setAsset(updated);
      setNotice(t("broll.rangeSaved"));
      setRangeDescription("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSegmentBusy(false);
    }
  }

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      await onSubmit(rows);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (cancelling || !window.confirm(t("review.cancelConfirm"))) return;
    setCancelling(true);
    setError("");
    try {
      if (analysisBusy) await window.subtitler.cancelMediaAssetAnalysis(candidate.assetId);
      await onCancel();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setCancelling(false);
    }
  }

  const selectedEstimate = analysisTarget?.estimates.find((estimate) => estimate.detail === analysisDetail);
  const hasGroundedLibraryContent = Boolean(asset?.aiDescription || asset?.userDescription || asset?.segments.length);
  const candidateKind = mediaKindLabel(candidate.mediaKind, t);

  return (
    <main className="broll-review">
      <header className="silence-review-header">
        <div><h1>{t("broll.title")}</h1><p>{t("broll.progress", { current: index + 1, total: candidates.length, decided: rows.length })}</p></div>
        <button disabled={cancelling} onClick={() => void cancel()}><X size={17} /> {cancelling ? t("review.cancelling") : t("review.cancelRun")}</button>
      </header>
      <section className="broll-review-card">
        <div className="broll-review-preview" aria-busy={!preview && !previewError}>
          {!preview && !previewError && <span>{t("broll.preparingPreview")}</span>}
          {preview?.mediaKind === "video" && <video key={preview.url} src={preview.url} controls autoPlay muted playsInline />}
          {preview?.mediaKind === "image" && <img src={preview.url} alt={t("broll.previewAlt", { title: candidate.title })} />}
          {previewError && <span role="alert">{t("broll.previewUnavailable", { error: previewError })}</span>}
        </div>
        <div className="broll-review-title">
          <span className="library-asset-icon"><Eye size={20} /></span>
          <div><h2>{candidate.title}</h2><p>{t("broll.sourceSummary", { kind: candidateKind, start: candidate.startLine, end: candidate.endLine })}</p></div>
          <span className="status status-running">{t("broll.titleOnly")}</span>
        </div>
        <p>{t("broll.matchHelp")}</p>
        <dl className="broll-review-facts">
          <dt>{t("broll.transcript")}</dt><dd>{candidate.transcriptText || t("broll.noTranscript")}</dd>
          <dt>{t("broll.sourceAvailable")}</dt><dd>{candidate.mediaKind === "image" ? t("broll.stillImage") : `${formatTime(candidate.sourceStartSec)} – ${formatTime(candidate.sourceEndSec ?? candidate.sourceStartSec)}`}</dd>
          <dt>{t("broll.whyMatched")}</dt><dd>{candidate.reason || t("broll.defaultReason")}</dd>
          <dt>{t("broll.file")}</dt><dd>{candidate.assetPath}</dd>
        </dl>

        <div className="broll-review-actions">
          <button disabled={analysisBusy || segmentBusy} onClick={() => void openAnalysis()}><Sparkles size={16} /> {t("broll.analyzeEntireAi", { kind: candidateKind })}</button>
          {candidate.mediaKind === "video" && <button disabled={analysisBusy || segmentBusy} onClick={() => { setRangeOpen(!rangeOpen); setAnalysisTarget(null); }}><Eye size={16} /> {t("broll.addRange")}</button>}
        </div>

        {rangeOpen && candidate.mediaKind === "video" && (
          <section className="broll-range-editor">
            <div className="broll-range-times">
              <label>{t("broll.startTimecode")}<input value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} placeholder="0:00" /></label>
              <span>–</span>
              <label>{t("broll.endTimecode")}<input value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} placeholder={t("broll.videoEnd")} /></label>
            </div>
            <label>{t("broll.rangeShows")}<textarea value={rangeDescription} onChange={(event) => setRangeDescription(event.target.value)} placeholder={t("broll.rangePlaceholder")} /></label>
            <div className="button-row">
              <button disabled={segmentBusy || analysisBusy} onClick={() => void saveSegment()}>{segmentBusy ? t("common.saving") : t("broll.saveMyDescription")}</button>
              <button disabled={segmentBusy || analysisBusy} onClick={() => { try { void openAnalysis(parsedScope()); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } }}><Sparkles size={16} /> {t("broll.describeRangeAi")}</button>
            </div>
          </section>
        )}

        {analysisTarget && (
          <section className="broll-analysis-options">
            <strong>{analysisTarget.scope ? t("broll.analyzeSelected") : t("broll.analyzeEntire", { kind: candidateKind })}</strong>
            <p>{t("broll.privacy")}</p>
            <div className="analysis-detail-options">
              {analysisTarget.estimates.map((estimate) => (
                <button key={estimate.detail} className={analysisDetail === estimate.detail ? "selected" : ""} disabled={analysisBusy} onClick={() => setAnalysisDetail(estimate.detail)}>
                  <strong>{detailLabel(estimate.detail, t)}</strong>
                  {estimate.recommended && <small className="analysis-recommended">{t("media.recommended")}</small>}
                  <span>{t("media.frames", { prefix: estimate.adaptive ? t("media.expectedPrefix") : "", count: estimate.sampleCount.toLocaleString(), suffix: estimate.sampleCount === 1 ? "" : "s" })}</span>
                  <small>{t("media.estimated", { cost: estimate.estimatedCostUsd.toFixed(4) })}</small>
                </button>
              ))}
            </div>
            <div className="button-row">
              {analysisBusy
                ? <button onClick={() => void window.subtitler.cancelMediaAssetAnalysis(candidate.assetId)}>{t("broll.cancelAnalysis")}</button>
                : <button onClick={() => setAnalysisTarget(null)}>{t("common.back")}</button>}
              <button className="primary" disabled={analysisBusy || !selectedEstimate} onClick={() => void analyze()}><Sparkles size={16} /> {analysisBusy ? t("common.analyzing") : t("media.analyze")}</button>
            </div>
          </section>
        )}

        {notice && <div className="library-alert success">{notice}</div>}
        {asset?.segments.length ? (
          <div className="broll-saved-segments">
            <strong>{t("broll.savedRanges")}</strong>
            {asset.segments.map((segment) => <span key={segment.id}>{formatTimecode(segment.startMs)}–{formatTimecode(segment.endMs)} · {segment.description}</span>)}
          </div>
        ) : null}

        <label>
          {t("broll.wholeDescription")}
          <textarea value={description} onChange={(event) => {
            const text = event.target.value;
            setDescriptions((current) => ({ ...current, [candidate.id]: text }));
            if (decisions[candidate.id]?.decision === "describe") setDecisions((current) => ({ ...current, [candidate.id]: { candidateId: candidate.id, decision: "describe", description: text.trim() } }));
          }} placeholder={t("broll.wholePlaceholder", { title: candidate.title })} />
        </label>
        <button onClick={() => void window.subtitler.showItemInFolder(candidate.assetPath)}><FolderOpen size={16} /> {t("broll.showSource")}</button>
      </section>
      {error && <div className="field-error" role="alert">{error}</div>}
      <section className="silence-review-decisions">
        <button className={decisions[candidate.id]?.decision === "describe" ? "active" : ""} onClick={describe}><Check size={18} /> {t("broll.useWhole")}</button>
        <button disabled={!hasGroundedLibraryContent} className={decisions[candidate.id]?.decision === "use_library" ? "active" : ""} onClick={() => decide({ candidateId: candidate.id, decision: "use_library" })}><Check size={18} /> {t("broll.useSaved")}</button>
        <button className={decisions[candidate.id]?.decision === "reject" ? "active" : ""} onClick={reject}><X size={18} /> {t("broll.notMatch")}</button>
      </section>
      <footer className="silence-review-footer">
        <div className="silence-review-navigation">
          <button disabled={index === 0 || submitting} onClick={() => setIndex(index - 1)}><ArrowLeft size={17} /> {t("common.previous")}</button>
          <div className="silence-review-dots">{candidates.map((item, itemIndex) => <button key={item.id} className={`${itemIndex === index ? "current" : ""} ${decisions[item.id] ? "decided" : ""}`} aria-label={t("broll.confirmMatch", { number: itemIndex + 1 })} onClick={() => setIndex(itemIndex)} />)}</div>
          <button disabled={index === candidates.length - 1 || submitting} onClick={() => setIndex(index + 1)}>{t("common.next")} <ArrowRight size={17} /></button>
        </div>
        <button className="primary" disabled={rows.length !== candidates.length || submitting} onClick={() => void submit()}>{submitting ? t("broll.savingDescriptions") : t("broll.continue")}</button>
      </footer>
      <small className="silence-review-id">{t("broll.confirmationId", { id: reviewId })}</small>
    </main>
  );
}

function formatTime(seconds: number): string {
  return formatTimecode(seconds * 1000);
}

function detailLabel(detail: MediaAnalysisDetail, t: ReturnType<typeof useI18n>["t"]): string {
  return t(`media.detail.${detail}`);
}

function mediaKindLabel(kind: "video" | "image", t: ReturnType<typeof useI18n>["t"]): string {
  return kind === "video" ? t("media.video") : t("media.image");
}
