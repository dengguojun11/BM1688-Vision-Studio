# labelU Quality Policy And Pass-Fail Analysis

## 1. Purpose

This skill is a `labelU`-specific analysis skill for quality standards, pass/fail judgment, and quality-policy linkage.

Its job is to analyze issues or changes related to:

- quality policy definitions
- pass/fail rules
- rule source and recommendation logic
- scenario-based quality standards
- training, validation, and release gate consumption of quality results
- page, backend, and docs consistency for quality-related behavior

## 2. When To Use

Use this skill when:

- a quality standard needs to be changed or audited
- pass/fail results look wrong
- recommendation logic for policy or standard source is questioned
- release readiness conflicts with quality results
- training result interpretation no longer matches policy expectations
- quality-related pages or docs need to be aligned with implementation

## 3. Required Context

Before using this skill, read:

- `skills/projects/labelU/PROJECT.md`
- `skills/projects/labelU/maps/code-map.md`
- `skills/projects/labelU/maps/business-flow.md`
- the common `01-code-impact` skill when doing change impact analysis

Core repository areas to inspect first:

- `labelu/internal/application/service/yolo_quality_policy.py`
- `labelu/internal/application/service/yolo_quality_policy_recommendation.py`
- `labelu/internal/application/service/yolo_quality_assessment.py`
- `labelu/internal/application/service/yolo_sop_eval_analysis.py`
- `labelu/internal/application/service/yolo_sop_model_evaluation.py`
- `labelu/internal/application/service/yolo_model_workflow.py`
- `frontend/extensions/pages/yolo_quality_policies.html`
- `docs/03-质量标准/`
- relevant release or delivery docs in `docs/05-发布与交付/`

## 4. Main Analysis Angles

### A. Policy definition and source

Check:

- where policy rules are defined
- where standard source is derived from
- whether recommendation logic and stored policy align

### B. Pass/fail judgment logic

Check:

- core comparison thresholds
- metric source
- fallback rules
- scenario-level overrides

### C. Downstream consumption

Check whether quality outputs affect:

- model workflow views
- validation interpretation
- release readiness
- publish gate or readiness messaging

### D. Frontend linkage

Check:

- `yolo_quality_policies.html`
- related data shape
- policy list / form / detail behavior

### E. Documentation alignment

Check:

- `docs/03-质量标准/`
- whether current main docs still match current implementation
- whether historical design docs are still being read as primary docs

## 5. Execution Steps

### Step 1. Classify issue type

Classify the issue as one of:

- policy definition problem
- recommendation source problem
- pass/fail judgment problem
- downstream readiness interpretation problem
- frontend policy page problem
- docs mismatch

### Step 2. Trace rule source

Start from the visible symptom:

- page output
- API response
- workflow status
- release readiness state

Then trace back to:

- policy service
- recommendation service
- assessment service
- evaluation result source

### Step 3. Check metric and threshold consistency

Focus on:

- which metric is being used
- whether scenario rules override global defaults
- whether fallback logic is intended
- whether the docs still describe the same rule source

### Step 4. Check downstream impact

A quality-policy change may affect:

- training result interpretation
- edge validation readiness explanation
- release gate communication
- model workflow summaries

### Step 5. Check tests and docs

Identify:

- backend tests around quality policy or quality summary
- frontend verification points
- docs that need to be updated

## 6. Output Format

```md
## Analysis Summary

- Issue type:
- Main affected stage:
- Most likely rule source:

## Findings

- Policy definition path:
- Recommendation path:
- Pass/fail path:
- Downstream usage:
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

This skill focuses on quality standards and pass/fail logic.

If the main issue is about:

- BM1688 export or edge validation runtime, prefer the BM1688 / edge validation skill
- training job lifecycle, prefer the YOLO train diagnosis skill
- docs routing only, prefer the docs routing skill
