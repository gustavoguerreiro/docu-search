# DocuSearch

DocuSearch is a document ingestion, indexing and search platform exposed as a REST API. Users
register, upload documents (PDF, DOCX, TXT), track processing status, and search their content.
Later stages add optional hybrid search and LLM-powered question answering with references to
source chunks.

It is a personal portfolio project built to practice and demonstrate backend engineering with
MongoDB, asynchronous processing, application security, observability, Docker and
infrastructure-as-code.

## Current status

**Milestones 1 (foundation), 2 (MongoDB persistence) and 3 (authentication) are complete. Document
upload, processing and search do not exist yet.**

What exists and works today:

- FastAPI application with liveness and readiness endpoints under `/api/v1`
- Configuration from environment variables, structured JSON logging
- MongoDB client managed by the application lifecycle, required indexes created at startup, real
  readiness checks
- Data-access layer (users, documents, chunks, refresh tokens) with ownership enforced in queries,
  tested against a real MongoDB; not yet exposed through the API
- Authentication: registration and login endpoints, Argon2id password hashing, JWT access tokens,
  rotating refresh tokens with reuse detection; register, login, refresh, logout and `me` endpoints
- One error body for every failure: `{"error": {"code", "message", "details"?}}`
- Docker image and a Docker Compose stack (API, MongoDB, Redis)
- Linting, type checking, unit tests, and a GitHub Actions workflow

What does **not** exist yet: user accounts, document upload, processing, search, metrics
dashboards, and any AWS deployment. See [`docs/roadmap.md`](docs/roadmap.md) for the plan and real
progress.

## Requirements

| Tool | Needed for | Notes |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | running the app and tests locally | uses a Python 3.13 found on your machine, or downloads one (default uv behaviour) |
| GNU Make | the `make` shortcuts below | on Windows, e.g. `winget install ezwinports.make`, then run commands from Git Bash |
| Docker with Compose v2 | running the full stack | only for the Docker path |
| curl | `make health` | on Windows PowerShell use `curl.exe`, since `curl` is an alias there |

No cloud account, API key or credential is required.

## Quick start

### Option A — run the API locally (no Docker)

```bash
git clone git@github.com:gustavoguerreiro/docu-search.git
cd docu-search
make install   # creates .venv and installs the locked dependencies
make check     # lint, format check, type check and unit tests
make run       # API on http://127.0.0.1:8000 (auto-reload)
```

In another terminal:

```bash
curl -i http://127.0.0.1:8000/api/v1/health/live
curl -i http://127.0.0.1:8000/api/v1/health/ready
```

`/live` returns `200`. `/ready` returns **`503` in this mode**, because the API checks MongoDB at
`MONGODB_URI` (default `mongodb://localhost:27017`) and the Compose MongoDB has no host port. That is
the intended behavior of a readiness probe; use Option B for a fully ready stack, or point
`MONGODB_URI` at a MongoDB you run yourself.

Interactive API docs: <http://127.0.0.1:8000/docs>.

### Option B — run the whole stack with Docker

```bash
make up        # builds the image, starts api + mongo + redis, waits until healthy
make health    # calls the live and ready endpoints
make logs      # follow logs (Ctrl+C to stop following)
make down      # stop the stack; data volumes are kept
```

The API is published only on `127.0.0.1:8000`. MongoDB and Redis have no host ports: they are
reachable only from other containers on the Compose network. Data lives in the named volumes
`docusearch_mongo_data`, `docusearch_redis_data` and `docusearch_uploads_data` (uploaded files); `make down`
keeps them. To delete the data
deliberately, run `docker compose down -v`.

Use either option at a time: both listen on port 8000.

## Configuration

Settings are read from environment variables, optionally from a `.env` file (git-ignored).
[`.env.example`](.env.example) is the template:

```bash
cp .env.example .env
```

Only these variables are read by the application today; the rest of `.env.example` is a template
for later milestones and is ignored until then.

| Variable | Default | Values |
|---|---|---|
| `APP_ENV` | `local` | `local`, `test`, `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (case-insensitive) |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string; may contain credentials, never logged. Compose sets `mongodb://mongo:27017` for the container |
| `MONGODB_DB_NAME` | `docusearch` | letters, digits, `_`, `-` (max 38) |
| `MONGODB_TIMEOUT_MS` | `2000` | 100–60000; bounds connection / server selection so a dead database fails fast |
| `JWT_SECRET_KEY` | random per process | ≥ 32 characters; **required when `APP_ENV=production`**, never committed. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `JWT_ALGORITHM` | `HS256` | only `HS256` is accepted |
| `ACCESS_TOKEN_TTL_MINUTES` | `15` | 1–60 |
| `REFRESH_TOKEN_TTL_DAYS` | `14` | 1–90; a session ends this long after login (refreshing does not extend it) |
| `STORAGE_BACKEND` | `local` | only `local` exists |
| `MAX_UPLOAD_SIZE_MB` | `25` | 1–100; largest accepted upload. Enforced at the HTTP layer before the body is read and again while streaming |
| `STORAGE_LOCAL_ROOT` | `data/uploads` | where uploaded files are kept. Compose sets `/data/uploads`, backed by the `docusearch_uploads_data` volume |

Logs are one JSON object per line on stdout. Values under keys that look sensitive (password,
token, secret, authorization, ...) are redacted.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health/live` | Liveness: `200` while the process is up; does not check dependencies |
| GET | `/api/v1/health/ready` | Readiness: `200` when all dependency checks pass, otherwise `503` |
| POST | `/api/v1/auth/register` | Create an account. Always `202` with the same body, whether or not the email exists (no user enumeration). Password: 12-128 characters |
| POST | `/api/v1/auth/login` | Returns `access_token` (15 min), `refresh_token` and `expires_in`; `401` with one body for any failure |
| POST | `/api/v1/auth/refresh` | Body `{"refresh_token"}`. Returns a new token pair; the sent token stops working. Reusing a spent token ends the whole session |
| POST | `/api/v1/auth/logout` | Body `{"refresh_token"}`. Ends the session; always `204` |
| GET | `/api/v1/auth/me` | The authenticated account (`Authorization: Bearer <access_token>`). `401` codes: `not_authenticated`, `token_expired`, `invalid_token` |
| POST | `/api/v1/documents` | Multipart field `file` (PDF, DOCX or UTF-8 text, up to `MAX_UPLOAD_SIZE_MB`). `202` with the document (`pending`); `409` + `Location` if you already uploaded the same content; `413`, `415`, `400` for rejected files. The type is decided from the content, not the file name |

`/ready` runs its checks concurrently: `mongodb` (a `ping`, bounded by `MONGODB_TIMEOUT_MS`), `storage`
(the uploads directory is writable) and `indexes` (the required MongoDB indexes exist; created at startup,
retried until they do): `{"status":"ok","checks":{"mongodb":"ok","storage":"ok","indexes":"ok"}}`, or `503` with `"fail"` for what is wrong.
The body never contains error details. Redis is added when it is first used (Milestone 5).

## Developer commands

| Command | What it does |
|---|---|
| `make install` | Install locked dependencies (`uv sync --locked`) |
| `make run` | Run the API locally with auto-reload |
| `make test` | Unit tests with coverage (no services needed) |
| `make test-integration` | Integration tests against a throwaway MongoDB (needs Docker; cleans up after itself) |
| `make lint` / `make format` / `make format-check` | Ruff lint / format / verify formatting |
| `make typecheck` | mypy in strict mode |
| `make check` | All of the above checks and the tests |
| `make up` / `make down` / `make logs` / `make health` | Docker stack lifecycle |

Integration tests use a separate, ephemeral MongoDB (Compose profile `test`) published only on
`127.0.0.1:27018`; it never touches your dev data and is not started by `make up`. Set
`MONGODB_TEST_URI` to point the tests at another instance. If that instance is down, the tests fail
with instructions instead of being skipped.

Run `make help` for the list. Dependencies are pinned exactly in `pyproject.toml` and locked in
`uv.lock`.

## Troubleshooting

- **`port is already allocated` / address already in use:** something else is on port 8000, often
  the other run option. Stop it (`make down`, or `Ctrl+C` for `make run`).
- **Port 8000 stays busy after killing a terminal running `make run` (Windows):** an orphaned uvicorn
  worker can keep the socket. Find it with `netstat -ano | findstr :8000` and stop that PID. Closing
  `make run` with `Ctrl+C` does not leave one behind.
- **`make health` fails right after `make up`:** `make up` already waits for health checks; if you
  started the stack differently, wait a few seconds and retry, or look at `make logs`.
- **`curl` prints a PowerShell table instead of the response:** use `curl.exe`.
- **`make: command not found` on Windows:** install GNU Make (see Requirements) and use Git Bash.

## Repository layout

```
src/docusearch/    application code (api/, core/, db/ incl. repositories/, main.py)
tests/             unit/ (no services) and integration/ (real MongoDB, `make test-integration`)
docs/              architecture, roadmap, architecture decision records
Dockerfile         one image for the API (and, later, the worker)
docker-compose.yml local stack
.github/workflows/ CI
CLAUDE.md          instructions for AI-assisted sessions
```

## Planned architecture

A modular monolith: a FastAPI API plus a separately executed RQ worker, sharing one Docker image.
MongoDB stores users, document metadata and text chunks; Redis/RQ runs background processing;
uploaded files go through a storage abstraction (local filesystem first, S3 later); Prometheus and
Grafana provide observability. Only the API (with a MongoDB client) and the MongoDB and Redis containers exist so far; nothing uses Redis yet.

Details: [`docs/architecture.md`](docs/architecture.md). Decisions: [`docs/decisions.md`](docs/decisions.md).

Planned technology: Python 3.13 · FastAPI · Pydantic · PyMongo Async · MongoDB · Redis + RQ ·
Docker Compose · Prometheus · Grafana · pytest · Ruff · mypy · JWT + Argon2id · Terraform ·
GitHub Actions. (Already in use: Python, FastAPI, Pydantic, Docker Compose, MongoDB and Redis
images, pytest, Ruff, mypy, GitHub Actions.)

## AWS

Terraform definitions for a future AWS deployment (ECS Fargate, ECR, S3, MongoDB Atlas, managed
Redis, Secrets Manager, CloudWatch, ALB) are a *planned* deliverable. **No AWS resources have been
or will be created during development.**
