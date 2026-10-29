import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const nav = fs.readFileSync(
  path.join(repoRoot, "frontend/labelU-Kit/apps/frontend/src/components/Navigate/index.tsx"),
  "utf8",
);

assert.match(nav, /type TasksView = 'list' \| 'training' \| 'stats' \| 'training-analysis'/);
assert.match(nav, /pathname\.startsWith\('\/training-analysis'\)/);
assert.match(nav, /navigate\('\/training-analysis'\)/);

const trainingAnalysisIndex = nav.indexOf("数据集分析");
assert.ok(trainingAnalysisIndex >= 0, "missing 数据集分析 nav item");
