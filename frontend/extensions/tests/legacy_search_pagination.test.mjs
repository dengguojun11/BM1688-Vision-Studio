import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const annotation = fs.readFileSync(path.join(repoRoot, "frontend/extensions/pages/annotation_settings.html"), "utf8");
const training = fs.readFileSync(path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"), "utf8");

for (const id of [
  "aliasPageSize", "btnAliasPrev", "btnAliasNext", "aliasPageSummary",
  "recipePageSize", "btnRecipePrev", "btnRecipeNext", "recipePageSummary",
  "runtimePageSize", "btnRuntimePrev", "btnRuntimeNext", "runtimePageSummary",
]) assert.match(annotation, new RegExp(`id=\"${id}\"`));

assert.match(annotation, /label-aliases\?\"\+params\.toString\(\)/);
assert.match(annotation, /training-recipes\?\"\+params\.toString\(\)/);
assert.match(annotation, /runtime-templates\?\"\+params\.toString\(\)/);
assert.match(annotation, /aliasTotal=Number\(/);
assert.match(annotation, /recipeTotal=Number\(/);
assert.match(annotation, /runtimeTotalCount=Number\(/);

for (const id of [
  "autoTrainingPlanSearch", "autoTrainingPlanPageSize", "pagerAutoTrainingPlan",
  "evaluationDatasetSearch", "evaluationDatasetPageSize", "pagerEvaluationDatasets",
  "qualityApprovalSearch", "qualityApprovalPageSize", "pagerQualityApprovals",
]) assert.match(training, new RegExp(`id=\"${id}\"`));
assert.ok(training.includes('api("/auto-training-plans" + query'), "plan loader should include query parameters");
assert.match(training, /api\("\/training-evaluation-datasets" \+ query/);
assert.match(training, /query = "\?page=" \+ evaluationDatasetPage \+ "&size=" \+ evaluationDatasetPageSize/);
assert.match(training, /api\("\/quality-policy-approvals" \+ query/);
assert.match(training, /view=workspace&page=/);
assert.match(training, /renderAutoTrainingListPager\("pagerEvaluationDatasets"/);
assert.match(training, /renderAutoTrainingListPager\("pagerQualityApprovals"/);
assert.match(training, /evaluationDatasetSearch\) query \+= "&q="/);
assert.match(training, /qualityApprovalSearch\) query \+= "&q="/);
assert.match(training, /evaluationDatasetPage = 0; loadEvaluationDatasets\(\)/);
assert.match(training, /qualityApprovalPage = 0; loadQualityApprovalWorkspace\(\)/);
assert.doesNotMatch(training, /var keyword = evaluationDatasetSearch\.trim\(\)\.toLowerCase\(\)/);
assert.doesNotMatch(training, /var keyword = qualityApprovalSearch\.trim\(\)\.toLowerCase\(\)/);

console.log("legacy extension search and pagination contract: ok");
