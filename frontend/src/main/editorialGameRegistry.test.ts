import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { listEditorialGames, rememberEditorialGame } from "./editorialGameRegistry";
import type { RuntimePaths } from "./paths";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("editorial game registry", () => {
  it("keeps learned knowledge while moving the selected game to the front", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "subutl-games-"));
    roots.push(root);
    const paths = { stateRoot: root } as RuntimePaths;
    fs.writeFileSync(path.join(root, "editorial-game-knowledge.json"), JSON.stringify({
      schema_version: 1,
      games: [{ key: "older game", title: "Older Game", revision: 3, last_used_at_utc: "2025-01-01T00:00:00Z", knowledge: { bosses_enemies: ["Boss"] } }],
    }));

    rememberEditorialGame(paths, "New Game");
    rememberEditorialGame(paths, "Older Game");

    expect(listEditorialGames(paths).map((game) => game.title)).toEqual(["Older Game", "New Game"]);
    const stored = JSON.parse(fs.readFileSync(path.join(root, "editorial-game-knowledge.json"), "utf8"));
    expect(stored.games[0].knowledge.bosses_enemies).toEqual(["Boss"]);
    expect(stored.games[0].revision).toBe(3);
  });
});
