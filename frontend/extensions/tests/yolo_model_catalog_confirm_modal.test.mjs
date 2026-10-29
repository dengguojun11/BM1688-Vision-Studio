import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_model_catalog.html"),
  "utf8",
);

assert.match(page, /id="confirmModal"/);
assert.match(page, /function askConfirm\(title, message, action\)/);
assert.match(page, /getElementById\("btnConfirmAction"\)\.onclick = async function/);
assert.doesNotMatch(page, /\b(?:window\.)?confirm\s*\(/);
assert.match(page, /同步模型目录/);
assert.match(page, /删除第三方兼容标识/);

console.log("model catalog confirmation modal contract: ok");
