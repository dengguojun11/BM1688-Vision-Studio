import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const training = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);

assert.match(
  training,
  /<div class="auth-box" id="authBox" data-lu-auth-panel style="display:none;" aria-hidden="true">/,
  "the auth panel must start hidden while the shared session is being checked",
);

assert.match(
  training,
  /body\.labelu-embedded #authBox\s*\{\s*display:\s*none !important;\s*\}/,
  "the embedded training workspace must not render a second-login panel",
);

assert.match(
  training,
  /<details class="auth-manual"[\s\S]*?id="tokenInput"/,
  "standalone training access must retain the existing manual recovery path",
);
