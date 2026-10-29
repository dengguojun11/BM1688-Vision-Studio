import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const portal = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/assets/labelu_portal.js"),
  "utf8",
);
const training = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);
const bm = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_bm1688_exports.html"),
  "utf8",
);
const publish = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_model_publish.html"),
  "utf8",
);
const shellCss = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/assets/labelu_standalone_shell.css"),
  "utf8",
);

assert.match(
  portal,
  /href: "\/aicm-publish-environments"/,
  "portal aux menu should expose dedicated AICM publish environment page",
);
assert.match(
  portal,
  /function resolveAppUrl\(url\)[\s\S]*new URL\(target, window\.location\.href\)\.toString\(\)/,
  "portal navigation should resolve relative app urls against the current frame location",
);
assert.match(
  portal,
  /function navigateAppTo\(url\)[\s\S]*var target = resolveAppUrl\(url\);[\s\S]*window\.top\.location\.href = target;/,
  "navigateAppTo should promote an absolute target to the top frame",
);
assert.match(portal, /title: "工作台"/);
assert.match(portal, /title: "任务与标注"/);
assert.match(portal, /title: "训练中心"/);
assert.match(portal, /title: "模型中心"/);
assert.match(portal, /title: "进化中心"/);
assert.match(portal, /title: "系统管理"/);
assert.match(portal, /href: "\/evolution", label: "问题样本回流"/);
assert.match(portal, /href: "\/admin\?view=governance", label: "成员与标签管理"/);
assert.match(portal, /href: "\/providers", label: "预标注与推理能力"/);
assert.doesNotMatch(portal, /href: "\/workflows", label: "工作流"/);
assert.doesNotMatch(portal, /title: "算能部署"/);
assert.match(portal, /auxEntryLabel: "其他设置"/);
assert.doesNotMatch(portal, /辅助入口 \/ 配置/);
assert.match(publish, /系统管理 → 外部发布环境/);
assert.doesNotMatch(publish, /辅助入口 \/ 配置 → AICM 发布环境/);
assert.match(portal, /var BRAND_NAME = "7GAI进化平台"/);
assert.match(portal, /function renderBranding\(\)[\s\S]*document\.title = document\.title\.replace/);
assert.match(portal, /renderBranding\(\);[\s\S]*mountYoloToolkitShell/);
assert.match(
  portal,
  /function navigateTrainingHubTo\(url\)[\s\S]*frame\.src = target;[\s\S]*window\.location\.href = target;/,
  "training workflow navigation should stay inside the current embedded workspace",
);
assert.match(
  portal,
  /function switchTrainingHubTab\(tabId\)[\s\S]*isEmbeddedMode\(\)\) return navigateTrainingHubTo\(url\)/,
  "all primary workflow tabs should use the embedded navigation helper",
);
assert.match(
  portal,
  /role="tab" aria-selected="/,
  "primary workflow controls should expose tab semantics",
);
assert.match(
  portal,
  /\{ id: "autoTrainingPlan", label: "自动训练计划" \}/,
  "embedded training navigation should expose automatic training plans",
);
assert.match(
  portal,
  /autoTrainingPlan: "\/yolo-training\?embedded=1&panel=autoTrainingPlan"/,
  "automatic training plan navigation should preserve the embedded workspace shell",
);
assert.match(
  portal,
  /data-context-active=/,
  "the auxiliary entry should retain its selected state for auxiliary panels",
);
assert.match(
  portal,
  /renderTrainingSubNav\(activeTab\)[\s\S]*var topNav = !isInMainFrame\(\) \|\| isEmbeddedMode\(\);[\s\S]*data-hub-more-href/,
  "training hub dropdown links should render absolute hrefs in embedded mode",
);
assert.match(
  portal,
  /data-hub-more-href/,
  "training hub helper menu should still use the shared portal navigation path",
);
assert.doesNotMatch(
  portal,
  /\{ id: "reports", label: "AI 评估" \}/,
  "embedded training navigation should not duplicate job-level AI evaluation",
);
assert.match(
  portal,
  /reports: "\/yolo-training\?embedded=1&panel=reports"/,
  "legacy AI report deep links should remain compatible",
);
assert.match(
  portal,
  /compile: "\/yolo-bm1688-exports\?embedded=1"/,
  "training hub compile entry should preserve the embedded workspace shell",
);
assert.match(
  training,
  /function openBm1688Export\(exportId\)[\s\S]*params\.set\("embedded", "1"\)[\s\S]*params\.set\("export_id", String\(exportId\)\)/,
  "model library compile actions should use the same embedded route and optional detail id",
);
assert.match(
  training,
  /new URL\(bmUrl, window\.location\.href\)\.toString\(\)/,
  "training page iframe fallback should also absolutize the compile page target",
);
assert.match(
  bm,
  /new URL\(url, window\.location\.href\)\.toString\(\)/,
  "bm1688 page iframe fallback should absolutize the top-level route target",
);
assert.match(
  shellCss,
  /@media \(max-width: 48rem\)[\s\S]*\.lu-training-hub-more-menu \{[\s\S]*left: 0;[\s\S]*right: auto;[\s\S]*width: min\(320px, calc\(100vw - 24px\)\);/,
  "the auxiliary menu should stay inside a narrow viewport",
);
assert.match(
  bm,
  /处理发布阻断 · 导出 #[\s\S]*编译详情 · 导出 #/,
  "BM1688 detail modal titles should identify the export record explicitly",
);
