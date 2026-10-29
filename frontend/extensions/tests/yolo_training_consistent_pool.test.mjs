import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const html = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);

assert.match(html, /<th>数据集分类<\/th>/);
assert.match(
  html,
  /var datasetCategory = r\.dataset_category_name \|\| r\.category \|\| "未分类";/,
);
assert.match(html, /r\.dataset_category_key/);
