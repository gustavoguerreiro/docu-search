# DocuSearch — Architecture Decision Records

Short ADRs in a single file. Add a new numbered entry for each material decision; do not edit
accepted entries silently — supersede them with a new one.

Format: **Status** · **Context** · **Decision** · **Consequences**.

---

## ADR-0001 — Modular monolith with a separate worker

**Status:** Accepted (Milestone 0)

**Context:** The system needs an HTTP API and slow background processing. The project is a
single-developer portfolio exercise.

**Decision:** One codebase and one Docker image with two entrypoints (API, worker). No additional
microservices.

**Consequences:** Simple deployment and testing; shared domain code; the API and worker scale
independently as separate containers. Clear package boundaries are needed to keep the monolith
modular.

---

## ADR-0002 — Python 3.13, versions pinned deliberately

**Status:** Accepted (Milestone 0); library versions to be verified in Milestone 1

**Context:** Python 3.12 or 3.13 are acceptable. Python 3.13.13 is installed on the developer
machine, so local and container runtime can match.

**Decision:** Use Python 3.13 everywhere (local, Docker, CI). Pin dependencies and container image
tags to exact versions. Before pinning, verify each library's compatibility with 3.13.

**Consequences:** Reproducible builds. If a required library lacks 3.13 support, revisit and
supersede this ADR with 3.12.

---

## ADR-0003 — MongoDB with PyMongo Async

**Status:** Accepted (Milestone 0)

**Context:** The stack requires MongoDB; the official PyMongo driver provides an asynchronous API,
avoiding a separate ODM.

**Decision:** Use PyMongo's async API directly with explicit repository modules and
index-initialization code. Avoid heavy ODM layers.

**Consequences:** Fewer abstractions and full control over queries and indexes. Cross-collection
operations are not atomic, so ingestion must be designed for idempotency and recovery
(see architecture §5).

---

## ADR-0004 — RQ for background jobs

**Status:** Accepted (Milestone 0)

**Context:** Document processing must be decoupled from HTTP requests, with retries and
observable job state. A Redis-backed queue is sufficient at this scale.

**Decision:** Use Redis + RQ. RQ is synchronous and relies on `fork`, so the worker runs on Linux
(in Docker); it is not run natively on Windows.

**Consequences:** Simple operational model. The worker uses synchronous DB access (or runs its own
event loop per job) — to be decided and recorded in Milestone 5. Job state alone is not trusted
as the source of truth: document status in MongoDB is, with reconciliation for stuck jobs.

---

## ADR-0005 — Storage abstraction with local-first implementation

**Status:** Accepted (Milestone 0)

**Context:** Uploads are stored locally during development but should move to S3 later without
rewriting domain logic.

**Decision:** Define a storage interface (put/get/delete by server-generated key). Implement a
local filesystem adapter first; an S3 adapter is only prepared in Milestone 11 if at all.

**Consequences:** Domain code never handles filesystem paths or client filenames. Keys are opaque
and server-generated, which also prevents path traversal.

---

## ADR-0006 — AWS infrastructure is code-only, never provisioned

**Status:** Accepted (Milestone 0)

**Context:** The project must demonstrate infrastructure skills without incurring cost or risk.

**Decision:** Terraform and deployment scripts are written and statically validated locally.
`terraform apply/destroy`, ECR pushes and any resource creation are out of bounds during
development. Scripts must fail safely without configuration and require explicit confirmation for
destructive actions.

**Consequences:** A successful `terraform validate` or `plan` is documented as local validation,
never as proof of a working AWS deployment.

---

## ADR-0007 — LLM features are optional and off by default

**Status:** Accepted (Milestone 0)

**Context:** Core search must work without any paid provider or API key.

**Decision:** `LLM_ENABLED=false` by default; provider access sits behind an abstraction; a missing
key is an error only when the feature is enabled. Tests use deterministic doubles.

**Consequences:** Reproducible tests and no hidden costs. Q&A responses may only cite chunks that
were actually retrieved.

---

## ADR-0008 — uv, pyproject.toml and a lockfile for dependency management

**Status:** Accepted (Milestone 1)

**Context:** Dependencies must be pinned deliberately, including transitive ones, and installs
must be reproducible locally, in Docker and in CI.

**Decision:** Use `uv` with `pyproject.toml` (PEP 621 + dependency groups) and a committed
`uv.lock`. Direct dependencies use exact `==` pins; the lock pins the transitive set. The project
is a `src/` layout package built with `hatchling` (pinned). Commands run with `uv run --locked`
so a stale lock fails instead of silently re-resolving. Runtime dependencies are added only by the
milestone that needs them. Ruff (rule set includes security, bugbear, naive-datetime and async
checks) and mypy `--strict` are configured in `pyproject.toml`.

**Consequences:** One tool and one lockfile; fast, reproducible installs. Contributors need `uv`
installed. Exact pins mean dependency upgrades are deliberate commits (`uv lock --upgrade-package`).
Versions were taken from PyPI on 2026-10-09 and resolved successfully on CPython 3.13.13.

---

## ADR-0009 — Container image and local Compose stack

**Status:** Accepted (Milestone 1)

**Context:** The API and the future worker must run from one reproducible image, and the local stack
must be safe by default without credentials.

**Decision:**
- Multi-stage `Dockerfile`: a builder installs locked, non-dev dependencies with uv into a venv; the
  runtime stage copies only that venv, runs as non-root (uid 10001) and has no tests, docs or `.env`
  (excluded by `.dockerignore`, and never copied by the Dockerfile). The command is `CMD` only, so the
  worker reuses the image with a different command.
- Base images and tools are pinned to exact tags: `python:3.13.16-slim-bookworm`, uv `0.11.20`,
  `mongo:8.0.32` (the stable 8.0 line, which Atlas also offers; 8.3 is a rapid-release line),
  `redis:7.4.11-bookworm` (closer to what managed AWS Redis offers than Redis 8). Tags are not
  digest-pinned; digests would be stricter but costlier to maintain.
- Compose publishes only the API, on `127.0.0.1`. MongoDB and Redis have no host ports. MongoDB runs
  **without authentication**, acceptable only because of that isolation; enable auth before ever
  publishing its port. Redis uses AOF so queued jobs survive restarts.
- The `api` container runs with a read-only root filesystem, a tmpfs `/tmp`, all capabilities dropped
  and `no-new-privileges`.
- `make down` never removes volumes. There is deliberately no `depends_on` yet because the API does not
  use MongoDB or Redis; readiness (not startup order) will gate on them.

**Consequences:** Reproducible, small (~218 MB) image. Host-side tools cannot reach Mongo/Redis without
an explicit override; Milestone 2 decides how integration tests reach MongoDB. Uvicorn's first two
startup lines predate the app's logging setup and stay plain text.

---

## ADR-0010 — CI on GitHub Actions

**Status:** Accepted (Milestone 1)

**Context:** Every change must be checked the same way locally and in CI, on Linux, with the same
pinned toolchain.

**Decision:** One workflow with two independent jobs. `python` runs `uv sync --locked`, Ruff
(lint + format check), mypy and the unit tests using the same commands as the Makefile. `docker`
validates the Compose file, builds the image, starts the whole stack with `--wait` and calls the
live/ready endpoints. Permissions are `contents: read`, superseded runs are cancelled, jobs have
timeouts, and third-party actions are pinned to commit SHAs (the release is in a comment) because tags
can be moved. uv is pinned to the same version as the Dockerfile (`UV_VERSION`). `.gitattributes`
forces LF so the Makefile and Dockerfile work on Windows checkouts.

**Consequences:** Dependency updates (uv, actions, base images) are deliberate edits. The workflow was
validated with actionlint and by running its commands locally and in a clean Linux container, but it
has not run on GitHub yet; integration tests (MongoDB/Redis) and any coverage threshold are added when
those tests exist.

---

## ADR-0011 — MongoDB client lifecycle and readiness

**Status:** Accepted (Milestone 2)

**Context:** The API needs one shared MongoDB client with a clear lifetime, and readiness must report a
dead database quickly without taking the process down.

**Decision:**
- One `AsyncMongoClient` per process, created in the application lifespan and closed in its `finally`
  block; it is stored on `app.state` and passed explicitly, with no module-level singleton.
- Creation does not connect (driver behavior, verified), so the API starts and serves `/live` and
  `/ready` (503) while MongoDB is down, and recovers without a restart.
- `tz_aware=True` so datetimes read from MongoDB are UTC-aware, matching the project rule.
- `serverSelectionTimeoutMS` and `connectTimeoutMS` come from `MONGODB_TIMEOUT_MS` (default 2000), so a
  readiness probe fails fast; the probe adds its own `asyncio.timeout` for hung connections.
- `MONGODB_URI` is a `SecretStr`; logs carry the database name and exception *types* only.
- Operation-level timeouts (`timeoutMS`) are not set globally, because later bulk operations and index
  builds should not inherit a 2 s limit.

**Consequences:** Running the API on the host without a reachable MongoDB yields `503` on `/ready`
by design. The integration-test access path to MongoDB is decided in Milestone 2 step 2.

---

## ADR-0012 — Integration tests run against a throwaway MongoDB in a Compose profile

**Status:** Accepted (Milestone 2)

**Context:** Persistence code must be tested against a real MongoDB, locally and in CI, without
exposing the dev database or sharing its data.

**Decision:**
- A dedicated `mongo-test` service (same pinned image as dev) in the Compose profile `test`, published on
  `127.0.0.1:27018` only, with tmpfs storage and no named volume. It is not started by `make up`.
- `make test-integration` starts it, runs `pytest -m integration`, always removes it and returns pytest's
  exit code. `make down` also removes it (`--profile "*"`).
- Tests under `tests/integration/` are marked `integration` automatically; the unit run and CI's `python`
  job exclude them. Each test gets a uniquely named database (`docusearch_test_<random>`) that is dropped
  afterwards; the fixture refuses to drop a name without that prefix.
- An unreachable test database fails the test with instructions rather than skipping it.
- CI's `integration` job uses the same Compose profile, so the image and health check have one definition.

**Consequences:** Dev data is never touched by tests, and tests are isolated from each other. Running
them needs Docker. Alternatives considered: publishing the dev Mongo port (shares data, weakens the
no-host-port rule) and running tests inside a container (slower local loop).

---

## ADR-0013 — Indexes derived from access patterns, created at startup

**Status:** Accepted (Milestone 2); the documents listing index was amended by ADR-0014

**Context:** Required indexes must be created and verified, and each must serve a known query.

**Decision:**
- Indexes are declared once in `db/indexes.py` with explicit names. Each exists for a documented access
  pattern: login lookup (`users.email` unique), document listing (`owner_id, created_at`), per-user
  duplicate detection (`owner_id, sha256` unique, race-free under concurrent uploads), reconciliation of
  stuck documents (`status, updated_at`), ordered chunk reads and idempotent reprocessing
  (`document_id, chunk_index` unique), refresh-token lookup (`token_hash` unique), expiry cleanup (TTL on
  `expires_at`) and revoking a user's sessions (`user_id`).
- `ensure_indexes` is idempotent; an index with the same name but different options fails loudly instead
  of being replaced (that needs a migration). `verify_indexes` independently reports missing or drifted
  indexes.
- The API creates indexes at startup. If MongoDB is down then, it still starts, and the `indexes`
  readiness check retries until it succeeds (consistent with ADR-0011).
- Readiness checks run concurrently, so a probe costs the slowest check, not their sum (4.3 s -> 2.0 s
  measured with MongoDB down).
- Search text indexes are deferred to the search milestone, when visibility of partially processed
  documents is designed.

**Consequences:** Uniqueness is enforced by the database, not by check-then-insert code. Every index adds
write cost, so new ones need a stated query. Unique indexes fail to build if duplicate data already
exists. Between startup and the first successful setup, uniqueness is not enforced; readiness reports
that, but a deployment that ignores readiness could write duplicates. Multiple processes creating the
same indexes concurrently is safe.

---

## ADR-0014 — Repository layer, offset pagination with an `_id` tie-breaker

**Status:** Accepted (Milestone 2)

**Context:** Milestones 3–6 need data access with ownership enforced in the query. Writing the listing
query showed that the index from ADR-0013 could not serve a deterministic sort.

**Decision:**
- One small class per collection (`UserRepository`, `DocumentRepository`, `ChunkRepository`,
  `RefreshTokenRepository`) constructed from the database handle. No abstract base, protocol or generic
  CRUD layer: there is a single implementation, and the driver calls stay visible.
- Operations are limited to what milestones 3–6 name. Status transitions and claiming for processing
  are written with the worker (Milestone 5); search queries with Milestone 6.
- Ownership is in the filter (`{_id, owner_id}`); another user's record is reported as not found.
  Email normalization (`strip().lower()`) happens inside the repository so no caller can skip it.
- Constraint violations become domain errors (`EmailAlreadyExistsError`, `DuplicateDocumentError`,
  `DuplicateChunkError`) raised from the unique indexes, not from check-then-insert code. Refresh-token
  `revoke` is a conditional update returning whether this call revoked it.
- Records are frozen pydantic models; secret fields are excluded from `repr`.
- Lists use offset pagination (bounded `limit` <= 100) sorted by `(created_at desc, _id desc)`. The
  index `owner_created` became `owner_created_id` on `(owner_id, created_at desc, _id desc)` so the
  whole sort comes from the index and pages stay stable when timestamps tie. The old index is listed in
  `RETIRED_INDEXES` and dropped at startup; entries stay until no deployment can still have it.

**Consequences:** Offset pagination can drift if documents are inserted between page requests and gets
slower for very deep offsets; acceptable for per-user lists. Switching to keyset pagination later only
changes the repository methods. Services that span collections (delete document + chunks) must tolerate
a failure between steps.

---

## ADR-0015 — Password hashing and access-token primitives

**Status:** Accepted (Milestone 3)

**Context:** Authentication needs password storage and signed access tokens that fail closed.

**Decision:**
- **Passwords:** Argon2id via `argon2-cffi` with its defaults (3 iterations, 64 MiB, 4 lanes: RFC 9106
  low-memory profile). Hashing runs in a dedicated 4-thread pool, never on the event loop, which also
  caps memory at about 4 x 64 MiB however many logins arrive at once. Passwords above 1024 bytes are
  refused before hashing (bounding attacker-controlled work). `verify_password` returns `False` for any
  bad input instead of raising. `needs_rehash` supports upgrading parameters at login later.
- **Access tokens:** JWT, HS256 only (the settings type is `Literal["HS256"]`, so `none` and asymmetric
  algorithms are unreachable). Verification pins the algorithm list, requires `exp`, `iat`, `sub`, `iss`,
  `aud` and `jti`, checks issuer and audience, and requires `sub` to be a valid ObjectId. All failures
  raise one `InvalidTokenError` carrying no token or library text; `ExpiredTokenError` is a subtype so
  the server can log the difference. Lifetime defaults to 15 minutes (max 60).
- **Signing key:** `JWT_SECRET_KEY`, at least 32 characters whenever it is set. If unset outside
  production, a random key is generated per process; there is intentionally **no public default**,
  because `APP_ENV` defaults to `local` and a published placeholder would make tokens forgeable on any
  deployment that forgot to configure it. Production refuses to start without an explicit key, and
  rejects values beginning with `change-me`.
- The test suite turns PyJWT's key-length warning into an error.

**Consequences:** Per-process random keys mean local tokens die on restart and differ between
processes; run a single API process or set a key. HS256 means every verifier holds the signing key; if
other services must verify tokens, switch to an asymmetric algorithm (a new ADR). There is no
access-token revocation: tokens are short-lived and `jti` is available if a deny-list is ever needed.
