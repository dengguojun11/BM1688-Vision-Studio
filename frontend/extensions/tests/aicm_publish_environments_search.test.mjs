import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/aicm_publish_environments.html"),
  "utf8",
);

for (const id of [
  "filterKeyword",
  "envPageSize",
  "btnEnvPrev",
  "btnEnvNext",
  "envPageSummary",
  "envPageIndex",
]) {
  assert.match(page, new RegExp(`id="${id}"`));
}

assert.match(
  page,
  /new URLSearchParams\(\{ page: String\(state\.page\), size: String\(state\.size\) \}\)/,
);
assert.match(page, /environments\?" \+ params\.toString\(\)/);
assert.match(page, /params\.set\("q", keyword\)/);
assert.match(page, /state\.total = Number\(data\.total/);
assert.match(page, /state\.activeEnvironmentRecord = data\.active_environment/);
assert.match(page, /state\.searchTimer = setTimeout/);

console.log("AICM publish environment server-search contract: ok");
