# labelU Release And Delivery Check

## 1. Purpose

This skill is a `labelU`-specific analysis skill for the release and delivery chain.

Its job is to analyze issues or changes related to:

- model workflow visibility
- release readiness and delivery gate behavior
- publish-related downstream usage of training, quality, and validation results
- page, backend, and docs consistency for release and delivery flow

## 2. When To Use

Use this skill when:

- a model appears ready in one place but not another
- release or delivery readiness looks inconsistent
- workflow status does not match training / validation / quality results
- publish or delivery page behavior seems wrong
- release-related docs need to be reconciled with current implementation

## 3. Required Context

Before using this skill, read:

- `skills/projects/labelU/PROJECT.md`
- `skills/projects/labelU/maps/code-map.md`
- `skills/projects/labelU/maps/business-flow.md`
- the common `01-code-impact` skill when doing change impact analysis

Core repository areas to inspect first:

- `labelu/internal/application/service/yolo_model_workflow.py`
- `labelu/internal/application/service/yolo_aicm_publish.py`
- `labelu/internal/application/service/yolo_quality_assessment.py`
- `labelu/internal/application/service/yolo_edge_validation.py`
- `labelu/internal/application/service/yolo_bm1688_export.py`
- `frontend/extensions/pages/yolo_model_workflow.html`
- `frontend/extensions/pages/yolo_model_publish.html`
- `frontend/extensions/pages/yolo_delivery_hub.html`
- `docs/05-发布与交付/`
- related quality or validation docs if the gate logic depends on them

## 4. Main Analysis Angles

### A. Workflow aggregation

Check:

- how training, quality, BM1688, and validation states are aggregated
- whether workflow summary is computed from current source data

### B. Release readiness

Check:

- readiness indicators
- missing artifact interpretation
- quality or validation dependency
- publish gate messaging

### C. Publish path

Check:

- AICM publish or similar downstream integration path
- publish request assumptions
- whether required artifacts and evaluation results are enforced or only displayed

### D. Frontend linkage

Check:

- `yolo_model_workflow.html`
- `yolo_model_publish.html`
- `yolo_delivery_hub.html`
- related response fields and status rendering

### E. Documentation alignment

Check:

- `docs/05-发布与交付/`
- whether current docs match current delivery and publish logic

## 5. Execution Steps

### Step 1. Classify issue type

Classify the issue as one of:

- workflow aggregation problem
- release readiness problem
- publish path problem
- page status mismatch
- docs mismatch

### Step 2. Trace the visible symptom backward

Start from:

- workflow page
- publish page
- delivery hub page
- API response

Then trace back to the service that aggregates the status.

### Step 3. Check upstream dependency inputs

Focus on:

- training outputs
- quality assessment outputs
- BM1688 export status
- edge validation status
- publish integration config or response

### Step 4. Check user-facing consistency

Compare:

- backend truth
- page display
- docs wording

### Step 5. Check tests and docs

Identify:

- relevant backend tests
- relevant page or integration checks
- missing verification points
- docs to update

## 6. Output Format

```md
## Analysis Summary

- Issue type:
- Main affected stage:
- Most likely aggregation point:

## Findings

- Workflow path:
- Readiness path:
- Publish path:
- Frontend path:
- Upstream dependency impact:

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

This skill focuses on release and delivery behavior.

If the main issue is about:

- training lifecycle, prefer the YOLO train diagnosis skill
- BM1688 export or validation runtime, prefer the BM1688 / edge validation skill
- quality policy source or pass/fail logic, prefer the quality policy analysis skill
