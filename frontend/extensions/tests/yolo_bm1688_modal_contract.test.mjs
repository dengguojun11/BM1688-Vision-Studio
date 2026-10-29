import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_bm1688_exports_mockup.html"),
  "utf8",
);

assert.match(page, /居中弹窗详情/);
assert.match(page, /居中弹窗预期/);
assert.doesNotMatch(page, /抽屉|drawer/i);

console.log("BM1688 页面交互契约：居中弹窗");
