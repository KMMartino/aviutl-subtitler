import fs from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import {
  updatePackageVersion,
  versionFromTag,
} from "./set-release-version.mjs";

test("extracts stable and prerelease semantic versions", () => {
  assert.equal(versionFromTag("v1.2.3"), "1.2.3");
  assert.equal(versionFromTag("v1.2.3-rc.1"), "1.2.3-rc.1");
});

test("rejects malformed release tags", () => {
  for (const tag of ["1.2.3", "v1.2", "v01.2.3", "v1.2.3 nope", "v"]) {
    assert.throws(() => versionFromTag(tag));
  }
});

test("changes only package metadata version", () => {
  const original = {
    name: "subtitler-frontend",
    version: "0.1.0",
    build: { appId: "example" },
  };
  const updated = updatePackageVersion(original, "2.0.0");
  assert.deepEqual(updated, { ...original, version: "2.0.0" });
  assert.equal(original.version, "0.1.0");
});

test("committed package and lockfile versions agree", () => {
  const pkg = JSON.parse(
    fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  );
  const lock = JSON.parse(
    fs.readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"),
  );
  assert.equal(pkg.version, lock.version);
  assert.equal(pkg.version, lock.packages[""].version);
});
