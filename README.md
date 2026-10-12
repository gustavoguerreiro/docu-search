# DocuSearch

DocuSearch is a document ingestion, indexing and search platform exposed as a REST API. Users
register, upload documents (PDF, DOCX, TXT), track processing status, and search their content.
Later stages add optional hybrid search and LLM-powered question answering with references to
source chunks.

It is a personal portfolio project built to practice and demonstrate backend engineering with
MongoDB, asynchronous processing, application security, observability, Docker and
infrastructure-as-code.

## Current status

**Milestone 1 (project foundation) is complete. There are no product features yet.**

What exists and works today:

- FastAPI application with liveness and readiness endpoints under `/api/v1`
- Configuration from environment variables, structured JSON logging
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
`docusearch_mongo_data` and `docusearch_redis_data`; `make down` keeps them. To delete the data
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

Logs are one JSON object per line on stdout. Values under keys that look sensitive (password,
token, secret, authorization, ...) are redacted.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health/live` | Liveness: `200` while the process is up; does not check dependencies |
| GET | `/api/v1/health/ready` | Readiness: `200` when all registered dependency checks pass, otherwise `503` |

`/ready` currently has no dependency checks registered (MongoDB and Redis are not used yet), so it
always returns `200` with `{"status":"ok","checks":{}}`.

## Developer commands

| Command | What it does |
|---|---|
| `make install` | Install locked dependencies (`uv sync --locked`) |
| `make run` | Run the API locally with auto-reload |
| `make test` | Unit tests with coverage |
| `make lint` / `make format` / `make format-check` | Ruff lint / format / verify formatting |
| `make typecheck` | mypy in strict mode |
| `make check` | All of the above checks and the tests |
| `make up` / `make down` / `make logs` / `make health` | Docker stack lifecycle |

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
src/docusearch/    application code (api/, core/, main.py)
tests/             unit tests (integration tests arrive with MongoDB, Milestone 2)
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
Grafana provide observability. Only the API, MongoDB and Redis exist so far.

Details: [`docs/architecture.md`](docs/architecture.md). Decisions: [`docs/decisions.md`](docs/decisions.md).

Planned technology: Python 3.13 · FastAPI · Pydantic · PyMongo Async · MongoDB · Redis + RQ ·
Docker Compose · Prometheus · Grafana · pytest · Ruff · mypy · JWT + Argon2id · Terraform ·
GitHub Actions. (Already in use: Python, FastAPI, Pydantic, Docker Compose, MongoDB and Redis
images, pytest, Ruff, mypy, GitHub Actions.)

## AWS

Terraform definitions for a future AWS deployment (ECS Fargate, ECR, S3, MongoDB Atlas, managed
Redis, Secrets Manager, CloudWatch, ALB) are a *planned* deliverable. **No AWS resources have been
or will be created during development.**
