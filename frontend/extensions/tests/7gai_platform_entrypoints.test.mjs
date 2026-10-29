import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const routes = fs.readFileSync(
  path.join(repoRoot, "frontend/labelU-Kit/apps/frontend/src/routes.tsx"),
  "utf8",
);
const modules = fs.readFileSync(
  path.join(repoRoot, "frontend/labelU-Kit/apps/frontend/src/pages/platform/modules.ts"),
  "utf8",
);
const platformPages = ["ingestion", "providers", "annotationReview"]
  .map((name) =>
    fs.readFileSync(
      path.join(repoRoot, `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/${name}.tsx`),
      "utf8",
    ),
  )
  .join("\n");
const layout = fs.readFileSync(
  path.join(repoRoot, "frontend/labelU-Kit/apps/frontend/src/pages/platform/layout.tsx"),
  "utf8",
);
const brandedPages = [
  "annotation_stats.html",
  "strategy_integration_s3_config.html",
  "third_party_calls.html",
  "yolo_model_catalog.html",
  "yolo_model_publish.html",
  "yolo_training.html",
];
const extensionPagesDir = path.join(repoRoot, "frontend/extensions/pages");

function visibleHtml(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<(script|style|template)\b[^>]*>[\s\S]*?<\/\1>/gi, "")
    .replace(/<[^>]+>/g, " ");
}

for (const entry of ["ingestion", "providers", "annotation-review"]) {
  const moduleKey = entry === "annotation-review" ? "annotation-review" : entry;
  assert.match(modules, new RegExp(`key: '${moduleKey}'`));
  assert.match(routes, new RegExp(`path: '${entry}'`));
}
assert.match(platformPages, /request\.get\('\/v1\/ingestion-jobs'/);
assert.match(platformPages, /request\.get\('\/v1\/providers'/);
assert.match(platformPages, /request\.get\('\/v1\/annotation-reviews'/);
assert.match(modules, /7GAI\s*进化平台/);
assert.match(layout, /<span>\{PRODUCT_NAME\}<\/span>/);
for (const pageName of brandedPages) {
  const page = fs.readFileSync(
    path.join(repoRoot, "frontend/extensions/pages", pageName),
    "utf8",
  );
  assert.doesNotMatch(page, />[^<]*LabelU(?: 发布| 系统| 自动训练| 尚未| 导出| 本地| 对外)?[^<]*</,
    `${pageName} should not expose the legacy LabelU brand in visible text`);
}

for (const entry of fs.readdirSync(extensionPagesDir, { withFileTypes: true })) {
  if (!entry.isFile() || !entry.name.endsWith(".html")) continue;
  if (entry.name.startsWith("__tmp") || entry.name.startsWith("_")) continue;
  const page = fs.readFileSync(path.join(extensionPagesDir, entry.name), "utf8");
  assert.doesNotMatch(
    visibleHtml(page),
    /\bLabelU\b/i,
    `${entry.name} should not expose the legacy LabelU brand in visible HTML text`,
  );
}

console.log("7GAI platform entrypoints: ok");
