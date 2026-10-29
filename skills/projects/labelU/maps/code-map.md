# labelU Code Map

## 1. Backend Structure

- `labelu/main.py`
  - FastAPI app entry
  - extension page route exposure
  - CLI command registration
  - startup background jobs

- `labelu/internal/adapter/routers/`
  - HTTP route definitions
  - first-level API entry for most backend behavior

- `labelu/internal/application/service/`
  - main business orchestration layer
  - highest-value area for impact analysis

- `labelu/internal/adapter/persistence/`
  - data access layer
  - useful when a change may alter query shape, filtering, or persistence behavior

- `labelu/internal/domain/models/`
  - ORM models
  - useful when analyzing schema or entity-level behavior changes

- `labelu/internal/common/`
  - shared runtime utilities
  - config, auth, logging, deployment helpers, BM1688 helpers, training helpers, frontend integration

## 2. Frontend Structure

- `frontend/labelU-Kit/`
  - main frontend application source

- `frontend/extensions/pages/`
  - independent business pages
  - includes training, evaluation, publish, workflow, validation, and settings pages

- `frontend/extensions/assets/`
  - supporting scripts and style assets injected into pages or task views

- `frontend/extensions/tests/`
  - frontend-side verification files

## 3. High-Weight Business Areas

The following areas should be treated as impact hot spots:

- `labelu/internal/adapter/routers/yolo_train.py`
- `labelu/internal/application/service/yolo_*`
- `labelu/internal/common/bm1688_*`
- `labelu/internal/common/edge_*`
- `frontend/extensions/pages/yolo_*.html`

## 4. Common Cross-Layer Paths

Typical impact paths often look like:

- router -> service -> persistence -> model
- extension page -> backend route -> service
- CLI command -> common helper / service
- startup hook -> watchdog / auto-resume service

## 5. Test Map

Useful test areas include:

- `labelu/tests/internal/common/`
- `labelu/tests/internal/application/`
- `frontend/extensions/tests/`

When a change touches training, validation, BM1688, quality policy, or frontend extension pages, these test areas should be checked first.
