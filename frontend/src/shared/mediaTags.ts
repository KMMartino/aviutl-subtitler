export const TAG_CATEGORIES = ["game", "platform", "subject", "action", "tone", "role", "category", "creator", "source", "format", "keyword"] as const;
export type TagCategory = typeof TAG_CATEGORIES[number];
export type MediaTag = {
  id: string;
  segmentId: string;
  category: TagCategory;
  value: string;
  origin: "manual" | "analysis" | "metadata";
  evidence: string;
  confidence: number;
};
export type TagFilter = { category: TagCategory; value: string };

export function normalizeTagValue(value: string): string {
  return value.normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase();
}

export function parseMediaTag(raw: string, strict = false): TagFilter {
  const text = raw.normalize("NFKC").trim().replace(/\s+/gu, " ");
  const colon = text.indexOf(":");
  const prefix = text.slice(0, colon).toLowerCase();
  const typed = colon > 0 && TAG_CATEGORIES.includes(prefix as TagCategory);
  const category = typed ? prefix as TagCategory : "keyword";
  const value = typed ? text.slice(colon + 1).trim() : text;
  if (!value || value.length > 160 || [...value].some((char) => char.charCodeAt(0) < 32) || strict && colon > 0 && !typed) {
    throw new Error("Use a supported category and a tag value of 1–160 characters.");
  }
  return { category, value };
}

export function parseTagQuery(query: string): { text: string; tags: TagFilter[] } {
  const tags: TagFilter[] = [];
  const text = query.replace(/\b(game|platform|subject|action|tone|role|category|creator|source|format|keyword):("(?:[^"\\]|\\.)*"|[^\s"]+)/giu,
    (_, category: string, value: string) => {
      const decoded: string = value.startsWith('"') ? JSON.parse(value) : value;
      tags.push(parseMediaTag(`${category}:${decoded}`, true));
      return " ";
    }).trim();
  return { text, tags };
}
