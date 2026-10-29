# labelU YOLO Train Diagnosis

## 1. Purpose

This skill is a `labelU`-specific diagnostic skill for the training and evaluation chain.

Its job is to analyze issues related to YOLO training flow, job lifecycle, evaluation linkage, auto-resume behavior, queue behavior, training result interpretation, and the surrounding extension pages and docs.

## 2. When To Use

Use this skill when:

- a training job fails, stalls, or never starts
- a job unexpectedly resumes or does not resume
- queue behavior seems wrong
- evaluation output is missing or inconsistent
- GT/Pred comparison or consistent-pool behavior is wrong
- training page data does not match backend state
- training-related docs need to be aligned with current implementation

## 3. Required Context

Before using this skill, read:

- `skills/projects/labelU/PROJECT.md`
- `skills/projects/labelU/maps/code-map.md`
- `skills/projects/labelU/maps/business-flow.md`
- the common `01-code-impact` skill when doing impact-oriented diagnosis

Core repository areas to inspect first:

- `labelu/internal/adapter/routers/yolo_train.py`
- `labelu/internal/application/service/yolo_train.py`
- `labelu/internal/application/service/yolo_train_queue.py`
- `labelu/internal/application/service/yolo_train_auto_resume.py`
- `labelu/internal/application/service/yolo_train_evaluation.py`
- `labelu/internal/application/service/yolo_train_gt_pred_compare.py`
- `labelu/internal/application/service/yolo_model_workflow.py`
- `frontend/extensions/pages/yolo_training.html`
- `frontend/extensions/tests/`
- `docs/02-训练与评估/`

## 4. Main Diagnosis Angles

### A. Job creation and scheduling

Check:

- route entry
- service call path
- task training readiness checks
- queue logic
- job status transitions

### B. Runtime and resume behavior

Check:

- watchdog start path from `labelu/main.py`
- `yolo_train_queue.py`
- `yolo_train_auto_resume.py`
- active job tracking
- user pause vs unexpected interruption distinction

### C. Training output and evaluation

Check:

- result path generation
- metrics collection
- evaluation trigger timing
- quality summary or downstream workflow consumption

### D. Frontend linkage

Check:

- `yolo_training.html`
- related extension assets
- training table rendering
- frontend tests around training list/detail behavior

### E. Docs alignment

Check whether the issue conflicts with:

- `训练、边端验证与发布现状.md`
- training SOP comparison docs
- node deployment docs

## 5. Execution Steps

### Step 1. Identify issue type

Classify the problem as one of:

- create/start problem
- queue/scheduling problem
- running/stall problem
- auto-resume problem
- evaluation/report problem
- frontend display problem
- doc mismatch problem

### Step 2. Trace the main code path

Start from the most likely entry:

- API route
- page action
- startup watchdog
- background service

Then trace inward through service and persistence layers.

### Step 3. Check state transitions

Focus on:

- job status values
- trigger timing
- active job guards
- retry or resume rules
- required artifacts on disk

### Step 4. Check downstream effects

A training issue may affect:

- evaluation reports
- GT/Pred comparison
- consistent pool
- model workflow page
- BM1688 export readiness
- release readiness

### Step 5. Check tests and docs

Identify:

- relevant unit tests
- frontend page tests
- missing test coverage
- docs that should be updated if behavior changed

## 6. Output Format

```md
## Diagnosis Summary

- Issue type:
- Main affected stage:
- Most likely code entry:

## Findings

- Backend path:
- Job state / runtime path:
- Frontend path:
- Downstream impact:

## Risks

- ...

## Verification

- Tests to run:
- Manual checks:

## Docs Impact

- Docs to review:
- Docs to update:
```

## 7. Scope Boundary

This skill focuses on the training and evaluation chain.

If the main issue is about:

- BM1688 export or edge validation, prefer the BM1688 / edge validation skill
- docs restructuring, prefer the docs routing skill
- generic code impact only, start with the common code-impact skill
