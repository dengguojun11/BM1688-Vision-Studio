import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const html = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);

assert.match(html, /input\.type = "checkbox"/);
assert.match(
  html,
  /#sampleConditionPicker input\[data-sample-condition-key\]:checked/,
);
assert.match(html, /function renderSampleConditionCounts\(counts\)/);
assert.match(html, /function syncSampleConditionWorkspace\(\)/);
assert.match(html, /function activateSampleConditionDimension\(key, shouldFocus\)/);
assert.match(html, /className = "sample-condition-dimensions"/);
assert.match(html, /className = "sample-condition-selected-list"/);
assert.match(html, /清空本维度/);
assert.match(html, /清空全部/);
assert.match(html, /sample_condition_option_counts/);
assert.match(html, /当前条件组合没有匹配到可训练图片/);
assert.doesNotMatch(
  html,
  /#sampleConditionPicker select\[data-sample-condition-key\]/,
);
