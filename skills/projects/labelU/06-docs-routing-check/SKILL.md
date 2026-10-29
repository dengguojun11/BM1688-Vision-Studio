# labelU Docs Routing Check

## 1. Purpose

This skill is a `labelU`-specific extension of the common `02-docs-consolidation` skill.

Its job is to determine where a document belongs inside the current `labelU` docs business-closure structure, whether it should remain primary, become supporting material, move to archive, or become a deletion candidate that still requires user confirmation.

## 2. When To Use

Use this skill when:

- a new document is added and needs a target module
- old documents need to be reorganized under the current `docs/` structure
- multiple `方案 / 分析 / 手册 / 现状 / 说明` docs overlap
- a module already has a main doc and you need to decide whether a new file should merge into it
- you want to judge whether a doc is current-state, reference-only, or historical

## 3. Required Context

Before using this skill, read:

- `skills/common/02-docs-consolidation/SKILL.md`
- `skills/projects/labelU/PROJECT.md`
- `skills/projects/labelU/maps/docs-map.md`
- `skills/projects/labelU/maps/business-flow.md`

When needed, also validate against:

- `docs/README.md`
- current module main docs
- current code paths in `labelu/` and `frontend/extensions/`

## 4. labelU Routing Rules

### Route to `docs/00-总览/` when:

- the document explains the overall project
- it is architecture-oriented
- it spans multiple business stages and is not owned by one module

### Route to `docs/01-任务与标注/` when:

- the topic is task, sample, attachment, pre-annotation, collaboration, AI labeling, or taxonomy

### Route to `docs/02-训练与评估/` when:

- the topic is training flow, evaluation reports, GT/Pred comparison, training SOP, or training node deployment

### Route to `docs/03-质量标准/` when:

- the topic is quality policy, pass/fail rules, threshold source, recommendation rules, or strategy design

### Route to `docs/04-边端验证与BM1688/` when:

- the topic is BM1688 export, edge validation, board-side validation, threshold connection to edge validation, or deployment verification around BM1688 artifacts

### Route to `docs/05-发布与交付/` when:

- the topic is model release, workflow readiness, delivery gate, or publish integration

### Route to `docs/06-运维与协作/` when:

- the topic is deployment, environment, logs, troubleshooting, collaboration workflow, or development records

### Route to `docs/99-历史方案归档/` when:

- the file mainly preserves historical design context
- it no longer reflects the primary current implementation
- it is useful as reference but harmful as current-state reading material

## 5. labelU Evaluation Rules

For each document, decide:

1. Which business module owns it?
2. Is it a main current-state document or only a supporting/reference document?
3. Does it duplicate another file’s topic or only overlap partially in content?
4. Is its core assumption still valid in the current codebase?
5. Should useful content be extracted into an existing main doc?
6. If the file is no longer needed, is it only a deletion candidate rather than immediately removable?

## 6. Execution Steps

### Step 1. Identify the topic

Classify the file into one or more of:

- overall architecture
- task and annotation
- training and evaluation
- quality policy
- edge validation and BM1688
- release and delivery
- ops and collaboration

### Step 2. Check the nearest current main doc

Look for the current main doc in that module first.

Decide whether the target file:

- should remain standalone
- should be merged into the main doc
- should become a supporting doc under the same module

### Step 3. Validate against implementation

Where possible, check whether the document still matches:

- backend route names
- service names
- page names
- workflow terminology
- storage path or runtime assumptions

### Step 4. Judge content overlap

Separate:

- duplicate file topic
- duplicate implementation explanation
- useful but historically framed content

### Step 5. Produce the routing decision

Allowed decision types:

- keep as primary
- keep as supporting doc
- merge into module main doc
- move to `99-历史方案归档`
- candidate for deletion after user confirmation

## 7. Output Format

```md
## Routing Result

- Source doc:
- Recommended module:
- Recommended role:

## Analysis

- Topic ownership:
- Current implementation match:
- Overlap:
- Preserved value:

## Action

- Keep / Merge / Archive / Candidate for deletion:
- If merge:
- If archive:

## Notes

- ...
```

## 8. Important Constraint

In `labelU`, this skill may recommend cleanup, but it must not directly delete documents unless the user explicitly confirms deletion.
