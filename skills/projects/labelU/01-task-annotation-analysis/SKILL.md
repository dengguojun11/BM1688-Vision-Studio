# labelU Task And Annotation Analysis

## 1. Purpose

This skill is a `labelU`-specific analysis skill for the task and annotation chain.

Its job is to analyze issues or changes related to:

- task creation and management
- sample and attachment handling
- pre-annotation
- collaboration and access behavior
- AI labeling linkage
- taxonomy and labeling preset support
- page, backend, and docs consistency for task/annotation behavior

## 2. When To Use

Use this skill when:

- a task list, task detail, or task permission behavior looks wrong
- samples or attachments are missing, duplicated, or inconsistent
- pre-annotation behavior is incorrect
- AI labeling does not match task flow expectations
- taxonomy or labeling preset logic affects annotation behavior
- task and annotation docs need to be reconciled with current implementation

## 3. Required Context

Before using this skill, read:

- `skills/projects/labelU/PROJECT.md`
- `skills/projects/labelU/maps/code-map.md`
- `skills/projects/labelU/maps/business-flow.md`
- the common `01-code-impact` skill when doing change impact analysis

Core repository areas to inspect first:

- `labelu/internal/adapter/routers/task.py`
- `labelu/internal/adapter/routers/sample.py`
- `labelu/internal/adapter/routers/attachment.py`
- `labelu/internal/adapter/routers/pre_annotation.py`
- `labelu/internal/adapter/routers/annotation_taxonomy.py`
- `labelu/internal/application/service/task.py`
- `labelu/internal/application/service/sample.py`
- `labelu/internal/application/service/attachment.py`
- `labelu/internal/application/service/pre_annotation.py`
- `labelu/internal/application/service/annotation_taxonomy.py`
- `labelu/internal/application/service/auto_label.py`
- `labelu/internal/application/service/ai_labeling_runtime.py`
- `labelu/internal/common/task_access.py`
- `labelu/internal/domain/models/`
- `docs/01-任务与标注/`

## 4. Main Analysis Angles

### A. Task lifecycle

Check:

- task CRUD path
- collaborator or access behavior
- task-level ownership and visibility rules

### B. Sample and attachment behavior

Check:

- sample creation and listing
- attachment upload/download/delete path
- media path or storage assumptions
- task-to-sample and sample-to-attachment linkage

### C. Pre-annotation and AI labeling

Check:

- pre-annotation read/write logic
- AI labeling runtime configuration
- how AI-generated results enter task or sample flow

### D. Taxonomy and labeling support

Check:

- taxonomy routes and services
- preset or global label dimensions
- whether task annotation depends on shared label definitions

### E. Docs alignment

Check:

- `docs/01-任务与标注/` current main doc
- whether task and annotation docs still match route, service, and page behavior

## 5. Execution Steps

### Step 1. Classify issue type

Classify the issue as one of:

- task management problem
- access / permission problem
- sample or attachment problem
- pre-annotation problem
- AI labeling problem
- taxonomy / label preset problem
- docs mismatch

### Step 2. Trace the main code path

Start from:

- route entry
- related page action
- service path

Then trace through persistence and model relationships as needed.

### Step 3. Check data and relationship consistency

Focus on:

- task-to-sample linkage
- sample-to-attachment linkage
- pre-annotation storage and retrieval
- access control assumptions
- taxonomy dependency assumptions

### Step 4. Check downstream impact

A task or annotation issue may affect:

- export behavior
- training readiness
- AI labeling usability
- annotation collaboration
- docs and onboarding material

### Step 5. Check tests and docs

Identify:

- relevant backend tests
- any related frontend or integration checks
- missing coverage
- docs to review or update

## 6. Output Format

```md
## Analysis Summary

- Issue type:
- Main affected stage:
- Most likely code entry:

## Findings

- Task path:
- Sample / attachment path:
- Pre-annotation / AI path:
- Taxonomy path:
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

This skill focuses on the task and annotation chain.

If the main issue is about:

- training lifecycle, prefer the YOLO train diagnosis skill
- docs routing only, prefer the docs routing skill
- generic cross-module impact only, start with the common code-impact skill
