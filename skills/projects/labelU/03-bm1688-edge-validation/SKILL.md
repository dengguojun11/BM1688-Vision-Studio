# labelU BM1688 And Edge Validation Diagnosis

## 1. Purpose

This skill is a `labelU`-specific diagnostic skill for the BM1688 export and edge-side validation chain.

Its job is to analyze issues across:

- ONNX export
- BM1688 compile/export flow
- board-side or edge-side validation
- threshold linkage into edge validation
- validation artifact packaging and result interpretation
- page, backend, and docs consistency for this chain

## 2. When To Use

Use this skill when:

- BM1688 export fails or stalls
- ONNX exists but bmodel is missing
- auto-resume for BM1688 export behaves unexpectedly
- edge validation results are missing, inconsistent, or not triggered
- board-side validation and SSH edge validation differ
- threshold recommendation or threshold access affects validation behavior
- validation-related docs need to be reconciled with current implementation

## 3. Required Context

Before using this skill, read:

- `skills/projects/labelU/PROJECT.md`
- `skills/projects/labelU/maps/code-map.md`
- `skills/projects/labelU/maps/business-flow.md`
- the common `01-code-impact` skill when doing change impact analysis

Core repository areas to inspect first:

- `labelu/internal/application/service/yolo_bm1688_export.py`
- `labelu/internal/application/service/yolo_bm1688_export_auto_resume.py`
- `labelu/internal/application/service/yolo_edge_validation.py`
- `labelu/internal/application/service/yolo_edge_validation_pack.py`
- `labelu/internal/application/service/yolo_edge_threshold_recommendation.py`
- `labelu/internal/application/service/yolo_edge_threshold_refinement.py`
- `labelu/internal/application/service/board_agent.py`
- `labelu/internal/application/service/board_eval_pack.py`
- `labelu/internal/application/service/edge_ssh_validation.py`
- `labelu/internal/common/bm1688_*`
- `labelu/internal/common/edge_*`
- `frontend/extensions/pages/yolo_bm1688_exports.html`
- `docs/04-边端验证与BM1688/`

## 4. Main Diagnosis Angles

### A. Export artifact generation

Check:

- route or job trigger entry
- export job row lifecycle
- ONNX generation path
- output directory structure
- compile prerequisites and timeout behavior

### B. Auto-resume and interrupted export behavior

Check:

- startup registration from `labelu/main.py`
- `yolo_bm1688_export_auto_resume.py`
- status scanning rules
- ONNX already-on-disk branch vs full re-export branch

### C. Validation path selection

Check which path is active:

- board agent path
- SSH edge validation path
- external cleanup or retention path
- validation packaging path

### D. Threshold integration

Check whether:

- edge threshold recommendation participates
- threshold refinement changes result interpretation
- validation page or backend uses stale threshold assumptions

### E. Frontend and docs alignment

Check:

- `yolo_bm1688_exports.html`
- related extension assets or tests
- `docs/04-边端验证与BM1688/` current main doc and reference docs

## 5. Execution Steps

### Step 1. Classify issue type

Classify the issue as one of:

- export trigger problem
- compile problem
- resume problem
- validation packaging problem
- board-side validation problem
- SSH edge validation problem
- threshold linkage problem
- page display / workflow mismatch
- docs mismatch

### Step 2. Trace the main execution path

Start from the most likely entry:

- page action
- backend API route
- startup watchdog
- post-training downstream trigger

Then trace service and helper calls inward.

### Step 3. Check job state and artifacts

Focus on:

- export status transitions
- ONNX existence
- bmodel existence
- packaging outputs
- edge validation records and result files

### Step 4. Check downstream business impact

A BM1688 or edge validation issue may affect:

- model workflow visibility
- release readiness
- publish gate expectations
- quality summary interpretation

### Step 5. Check tests and docs

Identify:

- relevant backend tests
- extension page tests
- missing verification points
- docs that need alignment

## 6. Output Format

```md
## Diagnosis Summary

- Issue type:
- Main affected stage:
- Most likely entry:

## Findings

- Export path:
- Compile / artifact path:
- Validation path:
- Threshold path:
- Frontend path:

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

This skill focuses on BM1688 export and edge validation.

If the main issue is about:

- generic training lifecycle, prefer the YOLO train diagnosis skill
- document routing only, prefer the docs routing skill
- quality policy design, prefer the quality policy analysis skill
