# CLAUDE.md — DocuSearch

DocuSearch: a REST API to ingest (PDF/DOCX/TXT), asynchronously process, and search documents.
Portfolio project; quality, security and clarity matter more than feature count.
Full architecture: `docs/architecture.md`. Plan and status: `docs/roadmap.md`. Decisions: `docs/decisions.md`.

## Current state
See `docs/roadmap.md`. Verify against the code and tests; never assume a milestone is complete
because a document says so.

## Stack (pinned versions, Python 3.13)
FastAPI, Pydantic/Pydantic Settings, PyMongo Async + MongoDB, Redis + RQ, Docker/Compose,
Prometheus + Grafana, pytest (async), Ruff, mypy, JWT + Argon2id, Terraform (code only),
Bash scripts, GitHub Actions. Modular monolith: one image, two entrypoints (API, worker).

## Session workflow (mandatory)
1. Start: check `git status`/branch, read this file and the roadmap, inspect code/tests, summarize
   state, propose ONE small task.
2. Before editing: state what/why/files/tests/out-of-scope.
3. Implement only that task. Never work on multiple milestones in one session.
4. Run relevant checks (tests, ruff, mypy, compose/terraform validation). Report only checks that
   actually ran, with real results.
5. Review `git diff`; update roadmap/docs when behavior changes.
6. End with: Implemented / Files changed / Verification / Known limitations / Commit suggestion /
   Next step. Then stop and wait.

## Git rules
Never commit, push, amend, reset or discard work without explicit permission. Preserve existing
uncommitted changes. Suggest a conventional commit message instead.

## Hard constraints
- No `terraform apply|destroy`, no AWS resources, no ECR pushes, no cloud costs, no assumed AWS creds.
- No paid external APIs in tests. LLM is off by default (`LLM_ENABLED=false`).
- No secrets in Git, images, logs, fixtures or terminal output. `.env` is git-ignored; use `.env.example`.
- No frontend, no extra microservices, no Kubernetes, no speculative features.

## Engineering rules
- Type hints everywhere; UTC-aware datetimes; central config via Pydantic Settings only.
- Don't block the event loop; manage Mongo/Redis clients via explicit lifecycle hooks.
- Enforce ownership in DB queries (`{_id, owner_id}`); never trust client-supplied user ids.
- Uploads are untrusted: server-generated storage keys, no paths in responses, size/type limits.
- Worker operations are idempotent; search only sees `completed` documents; no cross-collection
  atomicity assumptions.
- Metrics: bounded label cardinality (no user/document/request ids). Logs: structured JSON, no
  secrets or document content.
- API under `/api/v1`; accurate OpenAPI; consistent error schema.
- Prefer simple solutions; abstractions only at real boundaries (storage, search backend, LLM).
- Don't weaken tests, security checks or lint rules to get green.

## Commands
Tooling is `uv` (ADR-0008); dependencies are exact-pinned in `pyproject.toml` and locked in `uv.lock`.
Add a dependency with `uv add <pkg>==<version>` (or `--group dev`) and commit the updated lock.

```
make install       # uv sync --locked
make run           # uvicorn docusearch.main:create_app --factory --reload (127.0.0.1:8000)
make up            # docker compose up -d --build --wait (api + mongo + redis)
make down          # stop the stack; volumes are kept (never use `down -v` without asking)
make logs          # follow stack logs
make health        # curl live/ready on the running stack (127.0.0.1:8000)
make test          # pytest (unit, with coverage); excludes everything under tests/integration
make test-integration  # throwaway MongoDB (profile `test`, 127.0.0.1:27018) -> pytest -m integration -> cleanup
make lint          # ruff check
make format        # ruff format   (make format-check to verify only)
make typecheck     # mypy (strict)
make check         # all of the above
```

Code lives in `src/docusearch/`, tests in `tests/unit/` (and later `tests/integration/`).
App: `main.create_app(settings)` builds the FastAPI app (no import-time side effects); routers are
mounted under `/api/v1`. Dependency checks for `/health/ready` are registered in
`app.state.readiness_checks` (name -> async callable returning bool). Resource lifecycle (DB/Redis
clients) belongs in `main.lifespan`.
DB: `db/mongo.py` builds the PyMongo `AsyncMongoClient` (`tz_aware=True`, bounded timeouts); the lifespan
owns it (`app.state.mongo_client`, `app.state.mongo_db`) and registers the `mongodb` readiness check.
Never create clients at import time. The URI is a `SecretStr`: log only the db name / error types.
Integration tests: put them in `tests/integration/` (auto-marked `integration`); use the `mongo_client` /
`mongo_db` / `mongo_settings` fixtures, which give every test its own `docusearch_test_*` database and drop it.
Never point them at a non-test database. Unreachable MongoDB must fail, not skip.
Auth primitives: `core/passwords.py` (async Argon2id; never hash on the event loop) and `core/tokens.py`
(HS256 access tokens; one `InvalidTokenError` for callers). Never log tokens, hashes or the key; never
add algorithms to `decode`'s list. `JWT_SECRET_KEY` is required in production and random per process
otherwise. Unit tests in `tests/unit/core/test_tokens.py` are security-relevant: don't loosen them.
Uploads: the type of a file comes only from its bytes (`documents/filetypes.py`); the client's file name and
Content-Type are never inputs to a decision, and the sanitized name is display-only. Intake
(`documents/intake.py`) enforces the size limit while streaming and hashes before anything is stored.
`tests/unit/documents/` are security-relevant: don't loosen them.
Storage: callers use only the `Storage` protocol (`storage/__init__.py`); keys come from `new_storage_key()`
and are never derived from input; `LocalStorage` never overwrites and writes off the event loop. Its tests
include Linux-only security tests (symlink escape, file modes): run them via the Linux container check when
changing the adapter, don't rely on a Windows run. A new file-storage test must not write outside `tmp_path`.
Errors: raise `api.errors.ApiError(status, code, message)`; every failure uses the body
`{"error": {"code", "message", "details"?}}`. Validation errors must never echo submitted values.
Registration must stay indistinguishable for existing emails and login must verify against the dummy
hash for unknown emails (`auth/service.py`; covered by `tests/unit/auth/test_service.py`: don't loosen).
Protected routes: take `user: CurrentUser` (`api/dependencies.py`) and pass `user.id` as the owner to
repositories. Never read an owner/user id from path, query, body or headers. Another user's resource is a 404,
indistinguishable from a missing one. `tests/integration/test_auth_me_and_policy.py` pins this: don't loosen.
Refresh/logout endpoints live in `api/auth.py`; the schema bounds the token only loosely (4096) so every bad
token gets the same `401` from the service, never a `422`.
Refresh tokens: `auth/refresh_tokens.py` (`RefreshTokenService`: issue/rotate/revoke). Never store or log a raw
token; rotation order (create successor, then atomic revoke) and family revocation on reuse are
security-relevant, covered by `tests/integration/test_refresh_token_service.py`: don't loosen them.
Repositories: `db/repositories/<collection>.py`, one thin class each, built from `app.state.mongo_db`. Every
document/chunk operation takes `owner_id` and filters on it in the query. Add an operation only when a
milestone needs it; map unique-index violations to a domain error; no base classes or interfaces.
Test them in `tests/integration/test_repositories.py` against real MongoDB.
Indexes: defined only in `db/indexes.py` (`INDEXES`), each with an explicit name and a comment naming the
access pattern. Adding or changing one = edit there + an `explain()` test in `tests/integration/test_indexes.py`.
Changing an existing index's options needs a migration (startup fails loudly on conflict, by design).
Time/ids: use `core.clock.utc_now()` for every timestamp (never `datetime.now()`/`utcnow()`; ruff DTZ enforces
it). Convert ids only via `db.ids` (`parse_object_id`, `ObjectIdStr` for API params); never `ObjectId(x)` on
client input.
Config: add fields to `core/settings.py` (`Settings`) and mirror them in `.env.example`; in tests
build `Settings(_env_file=None)` so a developer's local `.env` never leaks in.
Logging: `core/logging_setup.py`; pass context via `extra=`, never inside the message string
(redaction is key-based).
CI: `.github/workflows/ci.yml` mirrors `make check` plus a Docker stack smoke test. Pin actions by commit
SHA, keep `UV_VERSION` equal to the Dockerfile's uv version, and keep CI commands identical to the Makefile.
Background servers (Windows/Git Bash): `$!` is not a Windows PID, so `taskkill` on it silently misses.
After any smoke test that starts a server, confirm with PowerShell `Get-NetTCPConnection -LocalPort <p>`
that the port is free; a `--reload` server can leave an orphaned worker holding the socket.
Docker: one `Dockerfile` (non-root, pinned bases) for API and worker; `docker-compose.yml` publishes only
the API on 127.0.0.1:8000. Mongo/Redis have no host ports. Pin new image tags exactly.
