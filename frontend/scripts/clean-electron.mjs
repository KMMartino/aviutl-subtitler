import fs from "node:fs";

fs.rmSync(new URL("../dist-electron", import.meta.url), {
  recursive: true,
  force: true,
});
