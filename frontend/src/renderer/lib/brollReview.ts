import type { BrollCandidate, BrollReviewDecision } from "./types";

export function applyBrollDecision(
  current: Record<string, BrollReviewDecision>,
  candidates: BrollCandidate[],
  candidate: BrollCandidate,
  decision: BrollReviewDecision,
): Record<string, BrollReviewDecision> {
  const next = { ...current, [candidate.id]: decision };
  if (!candidate.descriptionRequired && decision.decision === "use_library") {
    for (const other of candidates) {
      if (other.id !== candidate.id && other.startLine <= candidate.endLine && other.endLine >= candidate.startLine) {
        next[other.id] = { candidateId: other.id, decision: "reject" };
      }
    }
  }
  return next;
}
