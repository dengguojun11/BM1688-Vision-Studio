import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const html = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);

assert.equal((html.match(/data-create-wizard-panel="[123]"/g) || []).length, 3);
assert.equal((html.match(/data-create-wizard-indicator="[123]"/g) || []).length, 3);
assert.match(html, /<strong>选择数据<\/strong>/);
assert.match(html, /<strong>训练参数<\/strong>/);
assert.match(html, /<strong>确认训练<\/strong>/);
assert.match(html, /\.create-job-wizard-list[\s\S]*width: min\(920px, 100%\)/);
assert.match(html, /\.create-job-wizard-step button::after/);
assert.match(html, /<div class="form-section-hd">数据与任务<\/div>/);
assert.doesNotMatch(
  html,
  /<div class="form-section-hd"><span class="step-badge">1<\/span> 数据与任务<\/div>/,
);
assert.match(html, /id="btnCreateWizardPrev"[^>]*hidden/);
assert.match(html, /id="btnCreateWizardNext"/);
assert.match(html, /id="btnModalSubmit" hidden/);
assert.match(html, /function validateCreateWizardDataStep\(showFeedback\)/);
assert.match(html, /function validateCreateWizardParameterStep\(showFeedback\)/);
assert.match(html, /function renderCreateWizardReview\(\)/);
assert.match(
  html,
  /submit\.hidden = nextStep !== 3/,
  "the create action must only be shown on the confirmation step",
);
assert.match(
  html,
  /if \(currentCreateWizardStep === 1\)[\s\S]*validateCreateWizardDataStep\(true\)[\s\S]*validateCreateWizardParameterStep\(true\)/,
  "next must validate each completed step before advancing",
);
assert.match(
  html,
  /btnCreateWizardPrev\.onclick = function \(\)[\s\S]*showCreateWizardStep\(currentCreateWizardStep - 1\)/,
  "the previous action must navigate without resetting form state",
);
