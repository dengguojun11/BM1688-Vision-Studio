import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/annotation_settings.html"),
  "utf8",
);

assert.match(page, /id="modalConfirm"[\s\S]*id="btnConfirmAction"/);
assert.match(page, /function askConfirm\(message, action, title\)/);
assert.match(page, /getElementById\("btnConfirmAction"\)\.onclick = async function/);
assert.doesNotMatch(page, /\b(?:window\.)?confirm\s*\(/);
assert.match(page, /删除数据集分类/);
assert.match(page, /停用运行模板/);
assert.match(page, /删除标注预设/);
assert.match(page, /删除全局维度/);
assert.match(page, /确认导入标注设置/);

console.log("annotation settings confirmation modal contract: ok");
