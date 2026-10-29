# Docs Consolidation And Routing

## 1. Purpose

This skill is used to analyze a document set, identify duplication or overlap, decide which documents should be primary, supplemental, historical, or obsolete, and recommend where documents should live.

It is designed to be reusable across projects. The project-specific layer should only provide document directories, module grouping rules, and business flow names.

## 2. When To Use

Use this skill when:

- a repository has many overlapping docs
- a docs directory has become hard to navigate
- multiple方案/分析/手册/说明 documents exist for the same topic
- you need to decide whether to merge, archive, keep, rewrite, or delete docs
- you are building a modular documentation structure

Do not use this skill for legal or compliance retention decisions unless the retention rules are already provided by the user or the project.

## 3. Inputs

Preferred inputs:

- docs directory tree
- target document paths
- project module structure
- current business workflow grouping

Helpful optional inputs:

- code architecture summary
- feature ownership map
- historical document generation timeline
- deletion constraints

## 4. Core Questions

This skill should answer:

1. Which docs are still primary and useful?
2. Which docs partially overlap but should be merged or absorbed?
3. Which docs are historical references and should be archived?
4. Which docs are obsolete or duplicated?
5. Where should each topic live in the current docs structure?
6. What content should be extracted into a new primary document?

## 5. Analysis Principles

### Principle 1. Judge by current system reality, not by document age alone

A newer document can still be wrong, and an older document can still contain valid system knowledge.

### Principle 2. Separate file duplication from content duplication

Two files may not share a title but may still repeat the same implementation explanation.

### Principle 3. Use the current codebase as the source of truth

When possible, validate document claims against:

- current entry points
- current module names
- current pages or APIs
- current deployment or runtime behavior

### Principle 4. Keep one primary doc per topic where possible

For each major topic, prefer:

- one primary current-state doc
- optional supplemental docs
- optional archived historical docs

### Principle 5. Deletion and cleanup are separate from analysis

Analysis may recommend deletion, but actual deletion must follow project rules and user approval requirements.

## 6. Execution Steps

### Step 1. Build the current docs inventory

- list documents by directory
- group them by business topic or system module
- identify candidate clusters with similar titles or content

### Step 2. Map docs to current project structure

For each document, identify whether it maps to:

- current business module
- current implementation area
- historical design only
- mixed current and historical content

### Step 3. Evaluate each document

For each file, classify it as one of:

- primary current doc
- supporting doc
- archive/reference doc
- duplicate/absorbed doc
- obsolete doc

### Step 4. Detect overlap

Look for overlap in:

- feature descriptions
- architecture explanations
- workflow steps
- configuration guidance
- integration decisions
- implementation plans that were already absorbed elsewhere

### Step 5. Decide routing

For each topic, recommend:

- keep as-is
- merge into primary doc
- split into multiple docs
- archive
- mark obsolete
- delete later with approval

### Step 6. Extract reusable content

If a file is not worth keeping as a standalone document but still contains valid knowledge:

- extract the useful current-state content
- merge it into the proper primary document
- leave historical-only content in archive if needed

### Step 7. Produce the consolidation report

Summarize:

- retained docs
- merged topics
- archived docs
- obsolete docs
- recommended new doc structure
- deletion candidates that require approval

## 7. Output Format

Use this structure by default:

```md
## Docs Inventory Summary

- Topic:
- Current primary doc:
- Supporting docs:
- Archive docs:

## Findings

- File A:
- File B:

## Routing Decision

- Keep:
- Merge into:
- Archive to:
- Candidate for deletion:

## Extracted Content

- What should be preserved:
- Where it should move:

## Risks And Notes

- ...
```

## 8. Classification Rules

### Primary current doc

- aligns with current code and business flow
- has the clearest topic ownership
- can act as the main reading入口 for that topic

### Supporting doc

- adds useful detail
- does not compete with the primary doc
- may remain separate because it is more operational, tutorial, or detailed

### Archive/reference doc

- contains useful history or decision context
- not suitable as the current implementation source

### Duplicate/absorbed doc

- its useful content can be merged into another doc without losing meaning

### Obsolete doc

- depends on invalid assumptions, removed flows, or outdated architecture

## 9. Boundaries

- Do not delete files automatically unless explicitly authorized by project rules and the user.
- Distinguish “should not be primary anymore” from “should be deleted”.
- When the codebase cannot confirm a document claim, mark the conclusion as partial.

## 10. Adaptation Notes

To adapt this skill for a specific project:

- define the docs module structure
- define business-flow grouping
- define archive rules
- define deletion approval rules

This skill is intended to be the reusable common layer under project-specific document-routing skills.
