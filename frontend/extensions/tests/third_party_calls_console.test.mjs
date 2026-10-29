import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/third_party_calls.html"),
  "utf8",
);
const portal = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/assets/labelu_portal.js"),
  "utf8",
);
const publicPortal = fs.readFileSync(
  path.join(
    repoRoot,
    "frontend/labelU-Kit/apps/frontend/public/assets/labelu_portal.js",
  ),
  "utf8",
);

assert.match(page, /<h1>第三方调用中心<\/h1>/);
assert.match(page, /7GAI 进化平台 \/ 第三方调用中心/);
assert.match(page, /data-view="chains">业务链路/);
assert.match(page, /data-view="calls">调用明细/);
assert.match(page, /id="metricBlocked"/);
assert.match(page, /id="detailModal"/);
assert.match(page, /class="modal-backdrop"/);
assert.doesNotMatch(page, /drawer/);
assert.match(page, /\/api\/v1"\+path/);
assert.match(page, /<label for="filterType">业务类型<\/label>/);
assert.match(page, /value="dataset_creation">任务数据集创建/);
assert.match(page, /value="model_optimization">模型优化/);
assert.match(page, /value="threshold_experiment">阈值实验/);
assert.match(page, /business_type:f\.business_type/);
assert.match(page, /function businessTypeKey\(item\)/);
assert.match(page, /requestSize=f\.business_type\?100:state\.size/);
assert.match(page, /items\.filter\(function\(item\)\{return businessTypeKey\(item\)===f\.business_type;\}\)/);
assert.doesNotMatch(page, /window\.fetch\s*=\s*function/);
assert.match(page, /strategy_evolution:"策略演进平台"/);
assert.match(page, /experiment_platform:"实验平台"/);
assert.match(page, /labelu_auto_training:"7GAI 自动训练"/);
assert.match(page, /labelu:"7GAI 系统"/);
assert.match(page, /unknown:"未识别来源"/);
assert.match(page, /esc\(sourceSystemLabel\(x\)\)/);
assert.match(page, /esc\(sourceSystemLabel\(x\.source_system\)\)/);
assert.match(page, /esc\(sourceSystemLabel\(c\.source_system\)\)/);
assert.doesNotMatch(page, /request_payload|Authorization:\s*item/);
assert.match(
  portal,
  /\{ href: "\/third-party-calls", label: "第三方调用中心" \}/,
);
assert.equal(portal, publicPortal, "portal source and public copy must stay identical");

console.log("third-party calls console structure: ok");
