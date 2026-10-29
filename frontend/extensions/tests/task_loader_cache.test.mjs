import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const loader = fs.readFileSync(
  path.join(repoRoot, "frontend/labelU-Kit/apps/frontend/src/loaders/task.loader.ts"),
  "utf8",
);

assert.match(
  loader,
  /TASK_LIST_STALE_TIME_MS\s*=\s*30\s*\*\s*1000/,
  "tasksLoader should keep task list data fresh briefly when returning from toolkit pages",
);
assert.match(
  loader,
  /RECT_PRESET_SUMMARY_STALE_TIME_MS\s*=\s*60\s*\*\s*1000/,
  "tasksLoader should cache the heavier rect preset summary a little longer",
);
assert.match(
  loader,
  /Promise\.all\(\s*\[/,
  "tasksLoader should load task list and rect preset summary concurrently",
);
assert.match(
  loader,
  /queryKey:\s*taskKey\.list\(queryParams\)[\s\S]*?staleTime:\s*TASK_LIST_STALE_TIME_MS/,
  "task list fetchQuery should use the task-list stale time",
);
assert.match(
  loader,
  /queryKey:\s*\[\.\.\.taskKey\.labelSummary\(\), categoryScope\][\s\S]*?staleTime:\s*RECT_PRESET_SUMMARY_STALE_TIME_MS/,
  "label summary fetchQuery should use the summary stale time",
);
assert.match(
  loader,
  /queryKey:\s*taskKey\.datasetCategorySummary\(\)[\s\S]*?staleTime:\s*RECT_PRESET_SUMMARY_STALE_TIME_MS/,
  "dataset category summary fetchQuery should use the summary stale time",
);
