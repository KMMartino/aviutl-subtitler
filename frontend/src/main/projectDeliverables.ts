import fs from "node:fs";
import path from "node:path";

function copyFile(source: string, destination: string): void {
  if (path.resolve(source) === path.resolve(destination)) return;
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  if (fs.existsSync(destination)) {
    if (fs.readFileSync(source).equals(fs.readFileSync(destination))) return;
    throw new Error(`Output already exists and has changed: ${destination}`);
  }
  fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
}

/** Publish user-facing files; checkpoints and diagnostics stay in the project. */
export async function publishDeliverables(exo: string, destination: string): Promise<string[]> {
  const published = [destination];
  if (!fs.existsSync(exo)) throw new Error(`The completed EXO is missing: ${exo}`);
  const html = exo.replace(/\.exo$/i, ".html");
  if (fs.existsSync(html)) {
    const sourceRoot = path.dirname(html);
    const assetFolder = `${path.parse(destination).name}-assets`;
    const content = fs.readFileSync(html, "utf8").replace(/(src=["'])([^"']+)(["'])/g, (whole, before: string, reference: string, after: string) => {
      if (/^(?:data:|https?:)/i.test(reference)) return whole;
      const source = path.resolve(sourceRoot, decodeURIComponent(reference.replaceAll("&amp;", "&")));
      const relative = path.relative(sourceRoot, source);
      if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Report image must belong to its managed result folder.");
      copyFile(source, path.join(path.dirname(destination), assetFolder, relative));
      return `${before}${assetFolder}/${reference.replaceAll("\\", "/")}${after}`;
    });
    const target = destination.replace(/\.exo$/i, ".html");
    if (fs.existsSync(target) && fs.readFileSync(target, "utf8") !== content) throw new Error(`Output already exists and has changed: ${target}`);
    if (!fs.existsSync(target)) fs.writeFileSync(target, content, { flag: "wx" });
    published.push(target);
  }
  copyFile(exo, destination);
  const stem = path.parse(exo).name;
  const checkpoint = exo.replace(/\.exo$/i, ".json");
  if (fs.existsSync(checkpoint)) {
    const parts: { path: string }[] = JSON.parse(fs.readFileSync(checkpoint, "utf8")).outputs?.exo_parts ?? [];
    for (const [index, part] of parts.entries()) {
      if (index === 0) continue;
      const target = destination.replace(/\.exo$/i, `-part-${String(index + 1).padStart(2, "0")}.exo`);
      copyFile(part.path, target);
      published.push(target);
    }
  }
  for (const name of fs.readdirSync(path.dirname(exo)).filter((name) => name.startsWith(`${stem}.cut`) && name.endsWith(".mkv"))) {
    const target = path.join(path.dirname(destination), path.parse(destination).name + name.slice(stem.length));
    // Video copies can be large; leave the Electron event loop responsive.
    if (!fs.existsSync(target)) {
      try { await fs.promises.copyFile(path.join(path.dirname(exo), name), target, fs.constants.COPYFILE_EXCL); }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") await fs.promises.rm(target, { force: true });
        throw error;
      }
    }
  }
  for (const suffix of [".srt", ".vtt", ".chapters.txt"]) {
    const file = exo.replace(/\.exo$/i, suffix);
    if (fs.existsSync(file)) copyFile(file, destination.replace(/\.exo$/i, suffix));
  }
  return published;
}
