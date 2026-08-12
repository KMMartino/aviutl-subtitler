import fs from "node:fs";
import path from "node:path";
import type { EditorialCheckpointSummary } from "../renderer/lib/types";
import type { RuntimePaths } from "./paths";

type Registry = { paths: string[]; dismissed: string[] };

export function registerEditorialCheckpoint(paths: RuntimePaths, checkpoint: string): void {
  const registry = readRegistry(paths);
  const normalized = path.resolve(checkpoint);
  registry.paths = [normalized, ...registry.paths.filter((item) => !samePath(item, normalized))].slice(0, 100);
  registry.dismissed = registry.dismissed.filter((item) => !samePath(item, normalized));
  writeRegistry(paths, registry);
}

export function removeEditorialCheckpoint(paths: RuntimePaths, checkpoint: string): void {
  const registry = readRegistry(paths);
  const normalized = path.resolve(checkpoint);
  registry.paths = registry.paths.filter((item) => !samePath(item, normalized));
  registry.dismissed = [normalized, ...registry.dismissed.filter((item) => !samePath(item, normalized))].slice(0, 100);
  writeRegistry(paths, registry);
}

export function listEditorialCheckpoints(paths: RuntimePaths, lastInputPath = ""): EditorialCheckpointSummary[] {
  const registry = readRegistry(paths);
  const discovered = discoverBeside(lastInputPath);
  const dismissed = new Set(registry.dismissed.map(normalizedKey));
  const candidates = [...registry.paths, ...discovered]
    .filter((item, index, all) => all.findIndex((other) => samePath(other, item)) === index)
    .filter((item) => !dismissed.has(normalizedKey(item)) && fs.existsSync(item));
  const summaries = candidates.flatMap(readSummary).sort((left, right) => right.updatedAtUtc.localeCompare(left.updatedAtUtc));
  registry.paths = [...summaries.map((item) => item.path), ...registry.paths]
    .filter((item, index, all) => all.findIndex((other) => samePath(other, item)) === index)
    .slice(0, 100);
  writeRegistry(paths, registry);
  return summaries;
}

function readSummary(checkpoint: string): EditorialCheckpointSummary[] {
  try {
    const raw = JSON.parse(fs.readFileSync(checkpoint, "utf8")) as Record<string, unknown>;
    const sources = Array.isArray(raw.sources) ? raw.sources : [];
    const editorialMap = raw.editorial_map && typeof raw.editorial_map === "object"
      ? raw.editorial_map as Record<string, unknown>
      : {};
    const title = typeof raw.title_or_game === "string" ? raw.title_or_game.trim() : "";
    const objective = typeof raw.objective === "string" ? raw.objective.trim() : "";
    const updatedAtUtc = typeof raw.updated_at_utc === "string" ? raw.updated_at_utc : fs.statSync(checkpoint).mtime.toISOString();
    if (!title || !objective || !sources.length) return [];
    return [{
      path: path.resolve(checkpoint),
      title,
      objective,
      status: typeof editorialMap.status === "string" ? editorialMap.status : "unknown",
      sourceCount: sources.length,
      updatedAtUtc,
    }];
  } catch {
    return [];
  }
}

function discoverBeside(lastInputPath: string): string[] {
  if (!lastInputPath || !path.isAbsolute(lastInputPath)) return [];
  const directory = path.dirname(lastInputPath);
  try {
    return fs.readdirSync(directory)
      .filter((name) => name.toLocaleLowerCase().endsWith("editorial.json"))
      .map((name) => path.join(directory, name));
  } catch {
    return [];
  }
}

function registryPath(paths: RuntimePaths): string {
  return path.join(paths.stateRoot, "editorial-checkpoints.json");
}

function readRegistry(paths: RuntimePaths): Registry {
  try {
    const value = JSON.parse(fs.readFileSync(registryPath(paths), "utf8")) as Partial<Registry>;
    return {
      paths: Array.isArray(value.paths) ? value.paths.filter((item): item is string => typeof item === "string" && path.isAbsolute(item)) : [],
      dismissed: Array.isArray(value.dismissed) ? value.dismissed.filter((item): item is string => typeof item === "string" && path.isAbsolute(item)) : [],
    };
  } catch {
    return { paths: [], dismissed: [] };
  }
}

function writeRegistry(paths: RuntimePaths, registry: Registry): void {
  fs.mkdirSync(paths.stateRoot, { recursive: true });
  const target = registryPath(paths);
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(registry, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, target);
}

function normalizedKey(value: string): string {
  return path.resolve(value).toLocaleLowerCase();
}

function samePath(first: string, second: string): boolean {
  return normalizedKey(first) === normalizedKey(second);
}
