import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const semverPattern =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

export function versionFromTag(tag) {
  if (typeof tag !== "string" || !tag.startsWith("v")) {
    throw new Error(`Release tag must start with v, got: ${tag || "<empty>"}`);
  }
  const version = tag.slice(1);
  if (!semverPattern.test(version)) {
    throw new Error(
      `Release tag must contain a valid semantic version, got: ${tag}`,
    );
  }
  return version;
}

export function updatePackageVersion(packageJson, version) {
  return { ...packageJson, version };
}

function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes("--dry-run");
  const explicitTag = args.find((argument) => !argument.startsWith("--"));
  const tag =
    explicitTag ?? process.env.RELEASE_TAG ?? process.env.GITHUB_REF_NAME;
  const version = versionFromTag(tag);
  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const packagePath = path.resolve(scriptDir, "..", "package.json");
  const packageJson = JSON.parse(fs.readFileSync(packagePath, "utf8"));
  const updated = updatePackageVersion(packageJson, version);

  if (!dryRun) {
    fs.writeFileSync(
      packagePath,
      `${JSON.stringify(updated, null, 2)}\n`,
      "utf8",
    );
  }
  process.stdout.write(
    `${dryRun ? "Would set" : "Set"} package version to ${version}\n`,
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    main();
  } catch (error) {
    process.stderr.write(
      `${error instanceof Error ? error.message : String(error)}\n`,
    );
    process.exitCode = 1;
  }
}
