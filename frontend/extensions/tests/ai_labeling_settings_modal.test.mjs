import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/ai_labeling_settings.html"),
  "utf8",
);

assert.match(
  page,
  /\.modal-bg\s*\{[^}]*align-items:\s*center;[^}]*justify-content:\s*center;/s,
);
assert.match(page, /\.modal-bg \.modal\s*\{[^}]*max-height:\s*calc\(100vh - 32px\);/s);
assert.match(page, /body\.modal-open\s*\{\s*overflow:\s*hidden;\s*\}/);
assert.doesNotMatch(page, /drawer-open/);
assert.doesNotMatch(
  page,
  /\.modal-bg\s*\{[^}]*justify-content:\s*flex-end/s,
);
assert.doesNotMatch(page, /\.modal-bg \.modal\s*\{[^}]*height:\s*100vh/s);
assert.match(page, /document\.getElementById\("modalCancel"\)\.focus\(\)/);
assert.match(page, /if \(e\.key !== "Escape"\) return;/);
assert.match(page, /document\.body\.classList\.remove\("modal-open"\)/);
assert.match(page, /if \(e\.target\.id === "modalBg"\)/);

console.log("ai labeling settings centered modal contract: ok");
