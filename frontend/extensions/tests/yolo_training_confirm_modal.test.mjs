import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);

assert.doesNotMatch(page, /\b(?:window\.)?confirm\s*\(/);
assert.match(page, /class="modal-backdrop confirm-modal-backdrop" id="confirmActionModal"/);
assert.match(page, /function requestConfirmation\(message\)/);
assert.match(page, /return new Promise\(function \(resolve\) \{ pendingConfirmResolve = resolve; \}\);/);
assert.match(page, /event\.target === event\.currentTarget\) closeConfirmModal\(false\)/);
assert.match(page, /document\.getElementById\("confirmActionModal"\)\.classList\.contains\("show"\)\) closeConfirmModal\(false\)/);
assert.match(page, /await requestConfirmation\(msg\)/);

console.log("YOLO 训练确认弹窗契约：通过");
