# labelU Project Layer

## 1. Project Positioning

`labelU` is no longer only a multimodal annotation platform. In its current form, it is a single-repository workbench that covers:

- task and annotation data production
- AI-assisted labeling
- YOLO training and evaluation
- BM1688 export and edge-side validation
- quality policy and pass/fail judgment
- model release and delivery
- deployment and collaboration

This project layer exists to adapt reusable common skills to the real structure and business workflow of the `labelU` repository.

## 2. Repository Entry Map

### Backend

- Entry: `labelu/main.py`
- Routing: `labelu/internal/adapter/routers/`
- Persistence: `labelu/internal/adapter/persistence/`
- WebSocket: `labelu/internal/adapter/ws/`
- Service layer: `labelu/internal/application/service/`
- Responses: `labelu/internal/application/response/`
- Commands: `labelu/internal/application/command/`
- Domain models: `labelu/internal/domain/models/`
- Shared runtime helpers: `labelu/internal/common/`

### Frontend

- Main frontend source: `frontend/labelU-Kit/`
- Extension pages and injected assets: `frontend/extensions/`
- Independent business pages: `frontend/extensions/pages/`
- Extension assets and scripts: `frontend/extensions/assets/`
- Frontend validation or page tests: `frontend/extensions/tests/`

### Docs

- Main docs root: `docs/`
- Current business-closure grouping is the primary documentation structure

## 3. Core Business Closure

The recommended current-state reading and analysis order is:

1. task and annotation
2. training and evaluation
3. quality policy and pass/fail judgment
4. edge validation and BM1688
5. release and delivery
6. operations and collaboration

When adapting a common skill, prefer this business-closure model instead of classifying only by file type.

## 4. Documentation Grouping

The current docs structure is:

- `docs/00-总览/`
- `docs/01-任务与标注/`
- `docs/02-训练与评估/`
- `docs/03-质量标准/`
- `docs/04-边端验证与BM1688/`
- `docs/05-发布与交付/`
- `docs/06-运维与协作/`
- `docs/99-历史方案归档/`

Use these as the default routing targets for any documentation-consolidation skill.

## 5. Code Interpretation Rules

### Rule 1. `labelu/main.py` is a multi-role entry

It is not only the FastAPI startup entry. It also exposes:

- page routes for extension pages
- CLI commands
- startup background jobs
- deployment or verification helper commands

Impact analysis should treat changes in `labelu/main.py` as potentially cross-cutting.

### Rule 2. `frontend/extensions/pages/` contains major business tools

These are not minor demo pages. They carry important training, evaluation, validation, publish, and workflow capabilities.

### Rule 3. `yolo_train` is a hub area

Changes around YOLO training often affect:

- route registration
- training jobs
- model assets
- evaluation reports
- BM1688 export
- edge validation
- release readiness

### Rule 4. `docs/` is already business-routed

New project docs should not be dropped into the docs root without checking the target business module first.

## 6. Recommended Adaptation Strategy

### For `01-code-impact`

When used in `labelU`, the analysis should additionally map changes to:

- routers
- services
- extension pages
- tests
- docs module
- runtime / deploy / verification commands

### For `02-docs-consolidation`

When used in `labelU`, the analysis should additionally judge:

- which business module a doc belongs to
- whether it is a current-state primary doc
- whether it is a design reference doc
- whether it should move to `99-历史方案归档`
- whether deletion requires explicit user confirmation

## 7. Deletion And Cleanup Constraint

In this project, document deletion or cleanup must not be executed automatically unless the user explicitly confirms it first.

Analysis, routing, extraction, merge recommendations, and replacement planning can proceed first.

## 8. Project Layer Resources

This project layer is supported by:

- [maps/code-map.md](./maps/code-map.md)
- [maps/docs-map.md](./maps/docs-map.md)
- [maps/business-flow.md](./maps/business-flow.md)
