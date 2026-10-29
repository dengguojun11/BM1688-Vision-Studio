# LabelU

## Project Overview

LabelU is a multimodal data annotation platform (image/video/audio) built with FastAPI + SQLAlchemy. It provides task management, file attachment handling, sample annotation, and real-time collaboration via WebSocket.

## Architecture

Layered architecture following Domain-Driven Design principles:

- **Domain Layer**: `labelu/internal/domain/models/` - SQLAlchemy ORM models (User, Task, TaskSample, TaskAttachment, TaskPreAnnotation, TaskCollaborator, TaskSampleUpdater)
- **Application Layer**: `labelu/internal/application/` - Business logic split into:
  - `command/` - Request/input Pydantic models
  - `response/` - Response Pydantic models
  - `service/` - Business logic orchestration (uses `db.begin()` for transactions)
- **Adapter Layer**: `labelu/internal/adapter/` - External interfaces:
  - `routers/` - FastAPI HTTP route handlers
  - `persistence/` - CRUD database operations
  - `ws/` - WebSocket handlers
- **Common**: `labelu/internal/common/` - Shared utilities:
  - `config.py` - Pydantic BaseSettings configuration
  - `db.py` - SQLAlchemy engine, session, Base
  - `security.py` - JWT token creation, password hashing
  - `error_code.py` - Error codes enum + exception handlers
  - `converter.py`, `xml_converter.py`, `tf_record_converter.py` - Export format converters
  - `websocket.py` - WebSocket connection manager
- **Dependencies**: `labelu/internal/dependencies/user.py` - FastAPI dependency injection (auth)
- **Middleware**: `labelu/internal/middleware/` - Content-type and tracing middleware

## Development Commands

- **Run server**: `labelu` or `python -m labelu.main`
- **Run server with options**: `labelu --host 0.0.0.0 --port 8000 --media-host http://localhost:8000`
- **Run tests**: `python -m pytest labelu/tests/ -v`
- **Run single test**: `python -m pytest labelu/tests/path/test_file.py::test_name -v`
- **Format code**: `black labelu/`
- **Lint**: `flake8 labelu/`
- **DB migration**: `alembic -c labelu/alembic_labelu/alembic.ini upgrade head`
- **Install**: `poetry install`

## Key Files

- Entry point: `labelu/main.py`
- Config: `labelu/internal/common/config.py`
- Database: `labelu/internal/common/db.py`
- Auth: `labelu/internal/common/security.py` + `labelu/internal/dependencies/user.py`
- Error handling: `labelu/internal/common/error_code.py`
- Alembic migrations: `labelu/alembic_labelu/versions/`
- Tests: `labelu/tests/`
- Version: `labelu/version.py`

## Environment Variables

- `PASSWORD_SECRET_KEY`: JWT signing key (required in production)
- `DATABASE_URL`: DB connection string (default: `sqlite:///<data_dir>/labelu.sqlite`)
- `MEDIA_HOST`: Media file serving host URL (default: `http://localhost:8000`)

Data directory is determined by `appdirs.user_data_dir("labelu")`.

## Tech Stack

- Python 3.11, FastAPI ^0.90, SQLAlchemy ^1.4 (1.x style), Pydantic v1
- Auth: python-jose (JWT), passlib + bcrypt
- DB: SQLite (default) or MySQL (optional)
- Migrations: Alembic
- Package manager: Poetry
- WebSocket: websockets ^10

## Testing

- Tests in `labelu/tests/` using pytest
- Test DB: SQLite file `./test.db` (not in-memory)
- Test fixtures in `labelu/tests/conftest.py`
- Test user: `test@example.com` / `test@123`
- Tables (except `user`) are cleaned between tests via `autouse` fixture
- `scope="module"` for `client` and `testuser_token_headers` fixtures

## Conventions

- Routers delegate to services; services use CRUD persistence layer
- All responses wrapped in `OkResp[T]` or `OkRespWithMeta[T]` (GenericModel-based)
- Custom exceptions: `LabelUException` with `ErrorCode` enum
- Soft delete pattern: `deleted_at` timestamp on models
- Transaction management: `with db.begin():` blocks in service layer
- Session uses `autocommit=True` mode (legacy SQLAlchemy pattern)

## Workflow Rules

- Before modifying or optimizing a feature, first complete business analysis, code-path analysis, and page/API verification; only implement after the conclusion is confirmed.
- Every fix or feature change should include: analysis conclusion, local verification evidence, implementation, git commit, deployment, and live verification.
- Default delivery flow is: local code change -> local verification passes -> wait for the user to explicitly say "提交代码" -> git commit -> push -> production deploy -> live verification -> restore `local = git = production`.
- Do not commit code unless the user has explicitly authorized the commit for that round. Before that point, allow local code to be temporarily ahead of git/production for development and verification only.
- After the user authorizes commit, include the current round's intended retained changes in one submission. If a previous round was interrupted by network/power loss and left valid uncommitted changes, include them together in the next authorized commit instead of leaving multiple local versions around.
- After committing, deploy the change to the production server and verify the live result unless the user explicitly says not to deploy.
- Keep a single active code version: do not maintain multiple long-lived local variants, duplicate temp copies, or extra branches for the same work. Temporary local artifacts should be centralized under `.tmp/`.
- Current local development mode may use the production database through a local tunnel. In that mode, treat all local write operations as production-impacting changes and warn before risky verification steps that modify real data.
- Production verification default admin account:
  - Username: `admin@123.com`
  - Password: `Admin@123`
- Use the default admin account above for post-deploy UI verification unless the user provides a different verification account.
