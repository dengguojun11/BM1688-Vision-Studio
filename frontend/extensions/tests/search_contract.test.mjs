import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const annotation = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/annotation_settings.html"),
  "utf8",
);
const publish = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_model_publish.html"),
  "utf8",
);

for (const id of ["searchAliases", "searchRecipes", "searchPresets", "searchRuntime"]) {
  assert.match(annotation, new RegExp(`id="${id}"`));
}
assert.match(annotation, /label-aliases\?"\+params\.toString\(\)/);
assert.match(annotation, /training-recipes\?"\+params\.toString\(\)/);
assert.match(annotation, /runtime-templates\?"\+params\.toString\(\)/);
assert.match(annotation, /label-presets\?" \+ params\.toString\(\)/);

assert.match(publish, /id="filterPublishableKeyword"/);
assert.match(publish, /id="filterPublishHistoryKeyword"/);
assert.match(publish, /publishableKeyword\) q \+= "&q="/);
assert.match(publish, /historyKeyword\) q \+= "&q="/);

console.log("legacy extension search contract: ok");
