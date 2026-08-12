export function parseTimecode(value: string, emptyValueMs?: number): number {
  const compact = value.replace(/\s+/g, "");
  if (!compact) {
    if (emptyValueMs !== undefined) return emptyValueMs;
    throw new Error("Enter a timecode.");
  }
  const fields = compact.split(":");
  if (fields.length > 3 || fields.some((field) => field && !/^\d+(?:\.\d{1,3})?$/.test(field))) {
    throw new Error("Use seconds, minutes:seconds, or hours:minutes:seconds.");
  }
  const numbers = fields.map((field) => Number(field || 0));
  const seconds = numbers.at(-1) ?? 0;
  const minutes = numbers.length >= 2 ? numbers.at(-2) ?? 0 : 0;
  const hours = numbers.length === 3 ? numbers[0] : 0;
  if (seconds >= 60 || minutes >= 60 || !numbers.every(Number.isFinite)) {
    throw new Error("Minutes and seconds must be below 60.");
  }
  return Math.round((hours * 3600 + minutes * 60 + seconds) * 1000);
}

export function formatTimecode(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds % 3600 / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`
    : `${minutes}:${seconds.toString().padStart(2, "0")}`;
}
