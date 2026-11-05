# Target stack and project conventions (binding for all generated code)

You are part of an automated software-engineering pipeline. Several agents work on the same
workspace in parallel, so these conventions are a contract between you and the other agents.

## Stack
- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x (synchronous engine; ORM with `DeclarativeBase`
  and `Mapped[...]`/`mapped_column`), SQLite as the default database.
- Allowed third-party imports ONLY: `fastapi`, `starlette`, `pydantic`, `sqlalchemy`, `httpx`,
  `uvicorn`, `pytest`. Everything else must come from the Python standard library.
- No external services (no Redis, Kafka, Celery, Postgres server). Where production would use one,
  define a small interface (Protocol/ABC) with an in-process implementation and describe the
  production implementation in the docs.

## Layout
- `app/__init__.py` - package marker.
- `app/main.py` - MUST define `create_app(database_url: str | None = None) -> FastAPI`.
  No module-level app instance and no filesystem/database access at import time.
  Run with: `uvicorn app.main:create_app --factory`.
  Use a FastAPI `lifespan` handler (not the deprecated `on_event`) to create tables
  (`Base.metadata.create_all(engine)`); no migration tool in the prototype.
- `app/config.py` - settings from environment variables: `DATABASE_URL`
  (default `sqlite:///./data.db`) and `BASE_URL` (default `http://localhost:8000`).
  An explicit `database_url` argument to `create_app` overrides the environment.
- For SQLite engines pass `connect_args={"check_same_thread": False}`.
- Other modules exactly as listed in the design document (e.g. `app/models.py`, `app/schemas.py`,
  `app/repositories/...`, `app/services/...`, `app/api/...`).
- `contract/openapi.yaml` - the API contract. Routes, methods, status codes and request/response
  bodies in code and tests must match it exactly.
- `tests/conftest.py` - provides a `client` fixture:
  `with TestClient(create_app(f"sqlite:///{tmp_path}/test.db")) as client: yield client`
  (the `with` block makes the lifespan run).
- `tests/unit/` - fast tests of the public interfaces listed in the design.
- `tests/integration/` - HTTP-level tests of every contract endpoint via `TestClient`.

## Quality bar
- Type hints everywhere; small, focused modules; dependency injection via FastAPI `Depends`;
  state hangs off `app.state`, never module-level globals.
- Errors use `HTTPException` with the correct status code and the body `{"detail": "..."}`.
- Validate input with Pydantic models (e.g. URLs must be http/https, max length 2048).
- Time-dependent logic accepts an injectable clock so tests are deterministic.
- Tests: deterministic, no network, no sleeping. Redirect assertions use
  `client.get(path, follow_redirects=False)`.
- Never write placeholders, TODOs, `...` bodies or "rest of the code here". Every file is complete.
- Paths are workspace-relative POSIX paths. Never create `.env` files, keys or secrets.
