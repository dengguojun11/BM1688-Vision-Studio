import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/strategy_integration_s3_config.html"),
  "utf8",
);

assert.match(page, /id="confirmBackdrop"/);
assert.match(page, /function askDelete\(item\)/);
assert.match(page, /getElementById\("confirmOk"\)\.onclick = async function/);
assert.doesNotMatch(page, /\b(?:window\.)?confirm\s*\(/);
assert.match(page, /删除后该地址不能继续接收回流数据/);

console.log("strategy S3 confirmation modal contract: ok");
