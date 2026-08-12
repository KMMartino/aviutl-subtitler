import type { EditorialSourceSelection, MediaAnalysis } from "./types";

export type EditorialMediaCandidate = {
  path: string;
  analysis: MediaAnalysis;
};

const faceTerms = ["facecam", "face", "webcam", "camera", "cam", "selfie", "presenter"];
const gameTerms = ["gameplay", "game", "screen", "capture", "play", "program", "feed"];

export function buildEditorialSources(candidates: EditorialMediaCandidate[]): EditorialSourceSelection[] {
  const parsed = candidates.map((candidate, index) => ({ ...candidate, index, name: parsedName(candidate.path) }));
  const grouped = new Map<string, typeof parsed>();
  for (const candidate of parsed) {
    if (!candidate.name.delimiter) continue;
    const key = `${directory(candidate.path).toLocaleLowerCase()}\0${candidate.name.base.toLocaleLowerCase()}`;
    grouped.set(key, [...(grouped.get(key) ?? []), candidate]);
  }
  const consumed = new Set<number>();
  const output: Array<{ firstIndex: number; source: EditorialSourceSelection }> = [];
  for (const group of grouped.values()) {
    if (group.length !== 2) continue;
    const pair = pairCandidates(group[0], group[1]);
    if (!pair || !withinTenFrames(pair.audio.analysis, pair.visual.analysis)
      || (pair.basis === "manual" && !withinTenFrames(pair.visual.analysis, pair.audio.analysis))) continue;
    consumed.add(group[0].index);
    consumed.add(group[1].index);
    output.push({ firstIndex: Math.min(group[0].index, group[1].index), source: pairedSource(pair.audio, pair.visual, pair.basis, pair.confirmed) });
  }
  for (const candidate of parsed) {
    if (consumed.has(candidate.index)) continue;
    output.push({ firstIndex: candidate.index, source: singleSource(candidate) });
  }
  return output.sort((left, right) => left.firstIndex - right.firstIndex).map((item) => item.source);
}

export function setPairedAudioRole(source: EditorialSourceSelection, audioPath: string): EditorialSourceSelection {
  if (source.mode !== "paired") return source;
  const paths = [source.audioPath, source.visualPath];
  if (!paths.includes(audioPath)) return source;
  const visualPath = paths.find((path) => path !== audioPath) ?? source.visualPath;
  const audioIsCurrent = audioPath === source.audioPath;
  return {
    ...source,
    path: visualPath,
    audioPath,
    visualPath,
    audioDurationSeconds: audioIsCurrent ? source.audioDurationSeconds : source.visualDurationSeconds,
    visualDurationSeconds: audioIsCurrent ? source.visualDurationSeconds : source.audioDurationSeconds,
    durationSeconds: audioIsCurrent ? source.visualDurationSeconds : source.audioDurationSeconds,
    width: audioIsCurrent ? source.width : source.audioWidth,
    height: audioIsCurrent ? source.height : source.audioHeight,
    audioWidth: audioIsCurrent ? source.audioWidth : source.width,
    audioHeight: audioIsCurrent ? source.audioHeight : source.height,
    frameRate: audioIsCurrent ? source.frameRate : source.audioFrameRate,
    audioFrameRate: audioIsCurrent ? source.audioFrameRate : source.frameRate,
    roleConfirmed: true,
    pairingBasis: "manual",
  };
}

function pairCandidates(first: ParsedCandidate, second: ParsedCandidate): { audio: ParsedCandidate; visual: ParsedCandidate; basis: EditorialSourceSelection["pairingBasis"]; confirmed: boolean } | null {
  const firstRole = roleFromSuffix(first.name.suffix);
  const secondRole = roleFromSuffix(second.name.suffix);
  if (firstRole === "face" && secondRole !== "face") return { audio: first, visual: second, basis: "filename", confirmed: true };
  if (secondRole === "face" && firstRole !== "face") return { audio: second, visual: first, basis: "filename", confirmed: true };
  if (firstRole === "game" && secondRole !== "game") return { audio: second, visual: first, basis: "filename", confirmed: true };
  if (secondRole === "game" && firstRole !== "game") return { audio: first, visual: second, basis: "filename", confirmed: true };
  const firstPixels = pixels(first.analysis);
  const secondPixels = pixels(second.analysis);
  if (firstPixels > 0 && secondPixels > 0 && firstPixels !== secondPixels) {
    return firstPixels < secondPixels
      ? { audio: first, visual: second, basis: "resolution", confirmed: true }
      : { audio: second, visual: first, basis: "resolution", confirmed: true };
  }
  return { audio: first, visual: second, basis: "manual", confirmed: false };
}

function withinTenFrames(audio: MediaAnalysis, visual: MediaAnalysis): boolean {
  if (audio.durationSeconds === null || visual.durationSeconds === null) return false;
  const fps = visual.averageFrameRate ?? visual.nominalFrameRate ?? audio.averageFrameRate ?? audio.nominalFrameRate;
  if (fps === null || fps <= 0) return false;
  return Math.abs(audio.durationSeconds - visual.durationSeconds) <= (10 / fps) + 0.001;
}

function singleSource(candidate: ParsedCandidate): EditorialSourceSelection {
  return {
    path: candidate.path,
    durationSeconds: candidate.analysis.durationSeconds ?? 0,
    mode: "single",
    audioPath: candidate.path,
    visualPath: candidate.path,
    audioDurationSeconds: candidate.analysis.durationSeconds ?? 0,
    visualDurationSeconds: candidate.analysis.durationSeconds ?? 0,
    width: candidate.analysis.width,
    height: candidate.analysis.height,
    audioWidth: candidate.analysis.width,
    audioHeight: candidate.analysis.height,
    frameRate: candidate.analysis.averageFrameRate ?? candidate.analysis.nominalFrameRate,
    audioFrameRate: candidate.analysis.averageFrameRate ?? candidate.analysis.nominalFrameRate,
    pairingBasis: "single",
    roleConfirmed: true,
  };
}

function pairedSource(audio: ParsedCandidate, visual: ParsedCandidate, basis: EditorialSourceSelection["pairingBasis"], confirmed: boolean): EditorialSourceSelection {
  return {
    path: visual.path,
    durationSeconds: visual.analysis.durationSeconds ?? 0,
    mode: "paired",
    audioPath: audio.path,
    visualPath: visual.path,
    audioDurationSeconds: audio.analysis.durationSeconds ?? 0,
    visualDurationSeconds: visual.analysis.durationSeconds ?? 0,
    width: visual.analysis.width,
    height: visual.analysis.height,
    audioWidth: audio.analysis.width,
    audioHeight: audio.analysis.height,
    frameRate: visual.analysis.averageFrameRate ?? visual.analysis.nominalFrameRate ?? audio.analysis.averageFrameRate ?? audio.analysis.nominalFrameRate,
    audioFrameRate: audio.analysis.averageFrameRate ?? audio.analysis.nominalFrameRate,
    pairingBasis: basis,
    roleConfirmed: confirmed,
  };
}

type ParsedCandidate = EditorialMediaCandidate & { index: number; name: { base: string; suffix: string; delimiter: string } };

function parsedName(path: string): { base: string; suffix: string; delimiter: string } {
  const filename = path.replace(/\\/g, "/").split("/").pop() ?? path;
  const extensionIndex = filename.lastIndexOf(".");
  const stem = extensionIndex > 0 ? filename.slice(0, extensionIndex) : filename;
  const delimiters = Array.from(stem.matchAll(/[-.]/g), (match) => match.index).filter(
    (index) => index > 0 && index < stem.length - 1
  );
  const split = delimiters.find((index) => roleFromSuffix(stem.slice(index + 1)) !== "unknown")
    ?? delimiters.at(-1)
    ?? -1;
  return split > 0 && split < stem.length - 1
    ? { base: stem.slice(0, split), suffix: stem.slice(split + 1), delimiter: stem[split] }
    : { base: stem, suffix: "", delimiter: "" };
}

function roleFromSuffix(value: string): "face" | "game" | "unknown" {
  const normalized = value.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
  const face = faceTerms.some((term) => normalized.includes(term));
  const game = gameTerms.some((term) => normalized.includes(term));
  if (face === game) return "unknown";
  return face ? "face" : "game";
}

function pixels(analysis: MediaAnalysis): number {
  return (analysis.width ?? 0) * (analysis.height ?? 0);
}

function directory(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const index = normalized.lastIndexOf("/");
  return index < 0 ? "" : normalized.slice(0, index);
}
