import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const training = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);
const exportsPage = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_bm1688_exports.html"),
  "utf8",
);

assert.match(training, /function formatModelIdentityCell\(row\)[\s\S]*?strategyTaskMarkerHtml\(row\.strategy_task\)/);
assert.match(training, /function renderGlobalJobTaskCell[\s\S]*?strategyTaskMarkerHtml\(r\.strategy_task\)/);
assert.match(training, /function strategyTaskDisplayName\(row, value\)/);
assert.match(training, /function strategyTaskNameIsUnreadable\(value\)/);
assert.match(training, /function formatModelScopeDataCell\(row\)[\s\S]*?row\.dataset_category_name/);
assert.match(training, /row\.dataset_category_key/);
assert.match(training, /data\.class_order_signature/);
assert.match(training, /row\.class_names/);
assert.doesNotMatch(training, /if \(view === "list"\) loadStrategyEvolutionTable\(true\);/);
assert.doesNotMatch(training, /loadStrategyEvolutionTable\(true\)/);
assert.doesNotMatch(training, /panelTrainDomainAutomation|btnTrainDomainAutomation|trainDomainAutomationHost/);

assert.match(exportsPage, /function strategyTaskMarkerHtml\(marker\)/);
assert.match(exportsPage, /function strategyTaskDisplayName\(row, value\)/);
assert.match(exportsPage, /strategyTaskMarkerHtml\(r\.strategy_task\)/);

console.log("strategy task markers: ok");
