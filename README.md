# ChaosFlow — Pipeline Engine & REST API

Configurable, resilient data-pipeline platform (graduation project, backend track):
users define pipelines as a **node-and-edge JSON configuration**, run them through a
plugin-based DAG engine, and monitor live progress over WebSocket.

## Architecture

- **`engine/`** — framework-independent pipeline engine (no FastAPI/SQLAlchemy/HTTP imports):
  Pydantic v2 contract (`config.py`), decorator plugin registry (`registry.py`), safe filter
  expression grammar (`expression.py`, no `eval()`), sources/transforms/sinks plugins,
  topological DAG executor with retry/metrics/cancellation/events (`executor.py`), CLI (`__main__.py`).
- **`app/`** — FastAPI layer: JWT auth, pipeline CRUD with immutable versions, background run
  worker, WebSocket streaming, plugin catalog, health, structured JSON logging.
- **`alembic/`** — database migrations (`0001_initial`: users, pipelines, pipeline_versions,
  runs, run_logs, refresh_tokens).
- **`engine/schema/pipeline.schema.json`** — JSON Schema generated from the Pydantic models.
- **`examples/`** — sample pipeline + CSV data. **`tests/`** — 158 engine + API tests.

### Existing state → this implementation

The repository initially contained only the three PDF briefs (no code), so nothing was
preserved/rewritten — everything here is new. Requirements from `ChaosFlow_Backend_Tasks.pdf`
are fully implemented (engine, API, WS, tests); the overall project guide's YAML schema,
Kafka/Spark/HDFS/Airflow, ChaosLab, and AI module are explicitly **out of scope** for this
backend assignment (see Limitations).

### Document conflicts resolved

- **Config schema:** the project guide describes hierarchical YAML; the backend task specifies
  JSON nodes+edges. The JSON contract is canonical (`engine/config.py` + published schema).
- **Routes:** backend task uses `/auth/*`, `/pipelines*`, `/runs*`, `/plugins`, `/health`,
  `WS /ws/runs/{id}`; the guide prefixes `/api/*`. Both spellings are served (same handlers).
- **Roles:** backend task says `admin`/`user`; the guide lists `ADMIN/ENGINEER/VIEWER`.
  Implemented `admin`/`user` per the task. Public signup always creates `user`; seed the first
  admin via `ADMIN_EMAIL`/`ADMIN_PASSWORD`.

## Setup

Requires Python 3.11+ and (for production) PostgreSQL. Local dev works with SQLite.

```powershell
# 1. Install (Windows PowerShell; use python3 on Linux/macOS)
C:\Users\acer\AppData\Local\Programs\Python\Python313\python.exe -m pip install fastapi "uvicorn[standard]" sqlalchemy alembic pydantic pydantic-settings pyjwt bcrypt httpx "psycopg[binary]" python-multipart websockets pytest pytest-asyncio coverage ruff mypy
# or: pip install -e ".[dev]"

# 2. Configure
Copy-Item .env.example .env   # then edit secrets (never commit .env)

# 3. Migrate the database (PostgreSQL via docker-compose, or local SQLite fallback)
$env:DATABASE_URL = "postgresql+psycopg://chaosflow:chaosflow@localhost:5432/chaosflow"
python -m alembic upgrade head
# Verify on a clean DB: drop the DB (or delete ./chaosflow.db for SQLite) and re-run the command above.

# 4. Start the API (Swagger UI at http://localhost:8000/docs, OpenAPI at /openapi.json)
python -m uvicorn app.main:app --reload
# With Docker: docker compose up --build
```

Environment variables (see `.env.example`): `DATABASE_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`,
`ACCESS_TOKEN_EXPIRE_MINUTES` (30), `REFRESH_TOKEN_EXPIRE_DAYS` (7), `CORS_ORIGINS`,
`APP_VERSION`, `LOG_LEVEL`, `ENGINE_BATCH_SIZE` (500), `ENGINE_MAX_BACKOFF_SECONDS` (60),
`RUN_WORKER_THREADS` (4), `ADMIN_EMAIL`/`ADMIN_PASSWORD` (seed first admin).

## CLI (same engine as the API)

```powershell
python -m engine run examples/pipeline.json     # exit 0 ok, 1 failure, 2 usage, 130 cancelled
python -m engine generate-schema --out engine/schema/pipeline.schema.json
# Regenerate the published schema after any contract change:
python scripts/generate_schema.py
```

## API quick tour

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register`, `/auth/login`, `/auth/refresh` | Signup (always `user`), token pair, refresh rotation |
| GET/POST | `/pipelines` | List (paginated, ownership-isolated) / create (validated, version 1) |
| GET/PUT/DELETE | `/pipelines/{id}` | Read / update (new immutable version) / delete |
| GET | `/pipelines/{id}/versions` | Preserved version history |
| POST | `/pipelines/{id}/runs` | Start run → `202 {id, status}` (background worker) |
| POST | `/runs/{id}/stop` | Cancel (409 if already terminal; duplicate stops → 409) |
| GET | `/runs`, `/runs/{id}`, `/runs/{id}/logs` | History / details+metrics / persisted logs |
| WS | `/ws/runs/{id}?token=<JWT>` | Live `history` → `event`/`progress` → `done` |
| GET | `/plugins` | Live registry catalog with per-plugin config JSON Schema |
| GET | `/health` | Liveness + DB reachability |

Errors: `{"error": {"code", "message", "details"}}`. Auth: `Authorization: Bearer <access>`.
Progress over WS is a **graph-progress estimate** (`finished_nodes/total_nodes`, labelled in the
message) plus live record counters — not record-level completion.

## Tests, coverage, lint, types

```powershell
python -m pytest tests/ -q
python -m coverage run -m pytest tests/ -q; python -m coverage report   # engine total ≥ 80% (currently 88%)
python -m ruff check engine app tests scripts
python -m ruff format --check engine app tests scripts
python -m mypy engine app
```

## Plugin catalog

Sources: `csv`, `json` (lines+array), `rest_api` (GET/POST, page pagination, timeouts,
`max_pages`/`max_records` bounds), `postgres` (query; DSN via `dsn_env`/`dsn`; SQLite DSNs
accepted for dev/tests). Transforms: `filter` (safe grammar: comparisons incl. `in`,
`and/or/not`, arithmetic, `is None`; numeric strings coerce), `map` (rename/select/drop),
`cast` (`int/float/str/bool`, `on_error: fail|skip`), `aggregate` (group-by + sum/avg/min/max/count),
`join` (2 inputs, inner/left/right/outer, kept-key semantics documented in code), `deduplicate`
(cross-batch state, per run). Sinks: `csv`, `json` (array), `postgres`
(append/overwrite/upsert with `conflict_keys`; transactions per batch; SQLite emulation for tests),
`console`. Retry: `max_attempts` **includes** the initial attempt, exponential backoff
(`backoff_seconds * 2^(n-1)`, capped). Events: `node_started`, `batch_processed`,
`node_finished`, `node_failed`, plus `retry_attempt`, `cancelled`, `run_finished`.

## Limitations & follow-ups

- Runs execute in-process (thread pool): prompt start, no cross-restart durability; Postgres
  `LISTEN`/a real queue (Celery/RQ) would make execution durable.
- Engine materializes each node's full output in memory (batch streaming with bounded memory is future work).
- Exactly-once is **not** claimed: append sinks are at-least-once under retry; use upsert + conflict keys for idempotency.
- Single-node semantics only; no Kafka/Spark/HDFS/ChaosLab/AI (full-stack scope per project guide).
- Filter numeric coercion (`"150" > 100`) is a documented convenience for CSV/JSON string data.
