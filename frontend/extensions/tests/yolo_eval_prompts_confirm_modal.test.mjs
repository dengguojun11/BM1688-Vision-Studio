import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_eval_prompts.html"),
  "utf8",
);

assert.doesNotMatch(page, /\b(?:window\.)?confirm\s*\(/);
assert.match(page, /id="confirmActionModal"[\s\S]*id="confirmActionMessage"/);
assert.match(page, /function openConfirmModal\(message, action\)/);
assert.match(page, /if \(event\.target === event\.currentTarget\) closeConfirmModal\(\);/);
assert.match(page, /event\.key === "Escape"[\s\S]*closeConfirmModal\(\);/);
assert.match(page, /openConfirmModal\("有未保存的修改[\s\S]*loadPrompts\(true\);/);

console.log("YOLO 评估提示词确认弹窗契约：通过");
