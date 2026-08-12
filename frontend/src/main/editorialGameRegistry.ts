import fs from "node:fs";
import path from "node:path";
import type { RuntimePaths } from "./paths";

export type EditorialGameSummary = {
  title: string;
  lastUsedAtUtc: string;
  revision: number;
};

type StoredGame = Record<string, unknown> & {
  key?: string;
  title?: string;
  last_used_at_utc?: string;
  updated_at_utc?: string;
  revision?: number;
};

type Store = { schema_version: number; games: StoredGame[] };

export function editorialGameKnowledgePath(paths: RuntimePaths): string {
  return path.join(paths.stateRoot, "editorial-game-knowledge.json");
}

export function listEditorialGames(paths: RuntimePaths): EditorialGameSummary[] {
  return readStore(editorialGameKnowledgePath(paths)).games
    .filter((game) => typeof game.title === "string" && game.title.trim())
    .sort((left, right) => recency(right).localeCompare(recency(left)))
    .map((game) => ({
      title: String(game.title).trim(),
      lastUsedAtUtc: recency(game),
      revision: Math.max(0, Number(game.revision) || 0),
    }));
}

export function rememberEditorialGame(paths: RuntimePaths, rawTitle: string): EditorialGameSummary {
  const title = rawTitle.trim();
  if (!title || title.length > 240 || title.includes("\0")) throw new Error("Enter a valid game or project title.");
  const key = normalize(title);
  const store = readStore(editorialGameKnowledgePath(paths));
  const now = new Date().toISOString();
  let game = store.games.find((item) => item.key === key || normalize(String(item.title ?? "")) === key);
  if (game) {
    game.title = title;
    game.key = key;
    game.last_used_at_utc = now;
  } else {
    game = { key, title, revision: 0, created_at_utc: now, updated_at_utc: now, last_used_at_utc: now, knowledge: {} };
    store.games.push(game);
  }
  store.games.sort((left, right) => recency(right).localeCompare(recency(left)));
  store.games = store.games.slice(0, 80);
  writeStore(editorialGameKnowledgePath(paths), store);
  return { title, lastUsedAtUtc: now, revision: Math.max(0, Number(game.revision) || 0) };
}

function readStore(file: string): Store {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8")) as Partial<Store>;
    return { schema_version: 1, games: Array.isArray(parsed.games) ? parsed.games : [] };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw new Error(`Could not read game knowledge: ${String(error)}`, { cause: error });
    return { schema_version: 1, games: [] };
  }
}

function writeStore(file: string, store: Store): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(store, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, file);
}

function recency(game: StoredGame): string {
  return String(game.last_used_at_utc || game.updated_at_utc || "");
}

function normalize(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/g, " ");
}
