# Code Impact Analysis

## 1. Purpose

This skill is used to analyze the impact surface of a code change before or after implementation.

It is designed to work across different repositories by focusing on change paths, code entry points, related execution flow, tests, and documents rather than project-specific terminology.

## 2. When To Use

Use this skill when:

- a requirement is about to be implemented and you want to estimate the impact scope first
- a code diff already exists and you want to know what it may affect
- a bug fix needs a regression checklist
- you need to decide whether tests or documents must be updated
- you are reviewing a change and want a structured impact report

Do not use this skill as a replacement for full debugging when the main problem is runtime failure diagnosis. In that case, use a debugging-oriented skill first.

## 3. Inputs

Preferred inputs:

- changed files
- commit diff
- requirement description
- issue description
- stack trace or error description

Helpful optional inputs:

- target module names
- affected pages or API names
- related test file names
- related document paths

## 4. Core Questions

This skill should answer the following:

1. What modules were directly changed?
2. What code paths call into or depend on those modules?
3. What user-facing pages, APIs, jobs, scripts, or services may be affected?
4. What tests should be checked, added, or updated?
5. What documents should be updated?
6. What are the highest-risk regression points?

## 5. Execution Steps

### Step 1. Identify the direct change scope

- list the changed files
- group them by type: backend, frontend, config, test, docs, scripts, infra, other
- identify whether the change is additive, behavioral, structural, or cleanup-only

### Step 2. Find the nearest entry points

Look for the closest execution entry around the changed files, such as:

- HTTP routers or controllers
- service layer functions
- CLI commands
- scheduled jobs or workers
- page entry files
- shared utilities
- persistence or data access layer

### Step 3. Expand to dependent paths

Trace related dependencies in both directions where practical:

- who calls this code
- what this code calls
- what data structures or response shapes it changes
- what config flags or environment variables influence it

### Step 4. Identify user-visible or system-visible impact

Map the change to concrete outcomes:

- pages
- APIs
- background jobs
- exports
- training or processing pipelines
- deployment behavior
- logs or observability signals

### Step 5. Check tests and verification points

For each affected area, check:

- existing unit tests
- integration or API tests
- frontend or E2E tests
- manual verification points

If test coverage appears missing, call that out explicitly.

### Step 6. Check documentation impact

Determine whether the change affects:

- architecture overview
- developer docs
- operation manuals
- feature usage docs
- troubleshooting docs

### Step 7. Produce the impact report

Summarize:

- direct impact
- indirect impact
- regression risk
- tests to run
- docs to update
- open assumptions

## 6. Output Format

Use this structure by default:

```md
## Impact Summary

- Change type:
- Direct modules:
- Likely affected surfaces:

## Detailed Impact

- Backend:
- Frontend:
- Data / config:
- Tests:
- Docs:

## Risks

- Risk 1:
- Risk 2:

## Verification

- Must-run checks:
- Recommended manual checks:

## Assumptions

- ...
```

## 7. Severity Rules

Use these rough severity levels:

- High: may break user flow, API contract, data correctness, deployment, or production jobs
- Medium: may break local behavior, secondary pages, optional paths, or test assumptions
- Low: mostly structural, internal refactor, docs, or non-behavioral cleanup

## 8. Boundaries

- Do not claim an impact path unless it is supported by code structure, naming, references, or clear architectural inference.
- If a dependency path is inferred rather than directly observed, label it as an inference.
- If the repository is large and the evidence is partial, prefer a smaller correct impact report over a broad speculative one.

## 9. Adaptation Notes

To adapt this skill for a specific project:

- provide a project map
- define the real layering conventions
- define where docs live
- define where tests live
- define the business workflow names

This skill is intended to be the reusable common layer under project-specific impact-analysis skills.
