# labelU Docs Map

## 1. Current Primary Docs Structure

- `docs/00-总览/`
  - project entry, architecture overview, and end-user operation guide
  - `LabelU用户操作说明书.md` / `LabelU用户操作说明书-模型训练完整版.docx`

- `docs/01-任务与标注/`
  - task, sample, attachment, labeling, AI labeling

- `docs/02-训练与评估/`
  - training, evaluation, reports, training workflow references

- `docs/03-质量标准/`
  - quality policy, pass/fail logic, rule source, strategy docs

- `docs/04-边端验证与BM1688/`
  - BM1688 export, edge validation, threshold access, board-side validation

- `docs/05-发布与交付/`
  - release gate, delivery workflow, model workflow status

- `docs/06-运维与协作/`
  - deployment, collaboration, troubleshooting, dev log

- `docs/99-历史方案归档/`
  - historical design value, not the main current-state reading path

## 2. Routing Rules

### Put a document into `00-总览/` when:

- it explains the project as a whole
- it is a main entry or architecture overview
- it is not owned by a single business module

### Put a document into `01-06` when:

- it clearly belongs to one business-closure stage
- it explains current implementation, operation, or current-state behavior
- it should be discoverable from the active workflow path

### Put a document into `99-历史方案归档/` when:

- it still has historical value
- it is no longer the authoritative current implementation description
- it explains background decisions, historical方案, or superseded approaches

## 3. Consolidation Rules

- Prefer one main current-state document per topic.
- Keep design references only when they add value beyond the main doc.
- If a document is mostly absorbed by another main doc, keep only extracted useful content and consider the old file a candidate for archive or later deletion.
- If a document is outdated and misleading, do not keep it in the main module reading path.

## 4. Cleanup Constraint

In `labelU`, document deletion must be confirmed by the user first.

Safe actions before deletion:

- classify
- route
- mark overlap
- extract content
- merge useful sections into a new or existing primary doc
- propose deletion candidates
