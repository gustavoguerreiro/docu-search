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

---

## ADR-0016 — Refresh tokens: opaque, hashed, rotated per family with reuse detection

**Status:** Accepted (Milestone 3). Chosen as the default when the author did not select among the
options offered; revisit if the strict reuse behavior proves too aggressive.

**Context:** Access tokens are short-lived and cannot be revoked, so long-lived sessions need a
revocable credential whose theft is detectable.

**Decision:**
- A refresh token is 256 random bits (URL-safe, opaque). Only its SHA-256 hash is stored; a fast hash is
  appropriate because the secret is high-entropy (Argon2 is for passwords). The raw value exists only in
  the response that issues it.
- Each login starts a *family* (`family_id`). Every refresh creates the successor in the same family and
  atomically revokes the presented token. Presenting a token that is already revoked revokes the whole
  family and is logged (user and family ids, never the token).
- The successor is created before the old token is revoked, and if that revocation is lost to a
  concurrent request the family (successor included) is revoked. A detected reuse therefore never leaves
  a live token; a crash between the steps leaves an undelivered token that merely expires.
- The successor inherits the old token's expiry, so a session ends `REFRESH_TOKEN_TTL_DAYS` after login
  regardless of refreshes (absolute lifetime, no extra field).
- Logout revokes the family and is idempotent and silent for unknown tokens. All failures raise one
  `InvalidRefreshTokenError` with no detail. Malformed input is rejected before any database access.

**Consequences:** Users must log in again at least every `REFRESH_TOKEN_TTL_DAYS` days. A client that
retries a refresh after losing the response (so presents an already-rotated token) loses its session;
a short grace window could soften that at the cost of a larger replay window, and is not implemented.
Access tokens issued before a revocation stay valid until they expire (at most 15 minutes). The service
does not check that the user still exists; the endpoint layer must.

---

## ADR-0017 — Registration and login: no user enumeration, one error body

**Status:** Accepted (Milestone 3). The uniform-registration choice was made by default when the author
did not answer the question; revisit if the UX cost below matters more than the privacy gain.

**Context:** Without email delivery the API cannot tell an account owner "someone tried to register your
email", so the choice is between revealing existence (409) and a uniform answer.

**Decision:**
- `POST /auth/register` always answers `202` with the same body. The password hash is computed before
  the insert on every path, and a duplicate email is detected by the unique index, so neither the body,
  the status nor (approximately) the timing reveals whether the email exists. No tokens are issued at
  registration: the user logs in afterwards.
- `POST /auth/login` verifies the password against a real hash of a random password when the email is
  unknown, so both failure paths cost one Argon2 verification, and returns one `401` body for both.
- Password policy is length only: 12 to 128 characters at registration. Login accepts any length up to
  128 so older, shorter passwords are not locked out by a policy change. No composition rules.
- Token responses carry `Cache-Control: no-store`.
- Every error uses `{"error": {"code", "message", "details"?}}`. Validation details list location,
  message and type but never the submitted value (FastAPI's default echoes it, which would return a
  password to the caller and into proxies' logs). Unexpected errors are a generic `500`, logged
  server-side only.
- Refresh tokens travel in the JSON body (API-only service, no browser cookie flow, so no CSRF surface).

**Consequences:** A person who registers an email that already has an account receives the same
success response but cannot log in with the new password; with no email channel there is nothing better
to tell them. Timing equalization is approximate (a database lookup differs between paths) and is
tested with a generous ratio, not an exact bound. Account lockout and rate limiting do not exist yet
(Milestone 7), so password guessing is limited only by Argon2's cost. The error codes are part of the
public contract from now on.

---

## ADR-0018 — Refresh and logout endpoints

**Status:** Accepted (Milestone 3)

**Context:** The rotation rules of ADR-0016 need an HTTP surface whose failure modes leak nothing and
that is easy for a client to handle.

**Decision:**
- `POST /auth/refresh` takes the refresh token in the JSON body and returns a new access token and a new
  refresh token (`Cache-Control: no-store`). Every failure (unknown, malformed, wrong shape, expired,
  already used, revoked, owner deleted) is the same `401` with code `invalid_refresh_token`.
- The request schema only bounds the size (4096 characters); it does not validate token shape, so a long
  or odd token is a `401` like any other and clients need one rule, not two.
- If the token is valid but its user no longer exists, the refresh fails and all of that user's sessions
  are revoked. The existence check lives in `AuthService`, which knows both users and tokens; the
  refresh-token service deliberately does not.
- `POST /auth/logout` always answers `204`, with no body, for valid, spent, unknown and malformed tokens.
  It ends the session (the whole family), so logging out with the newest token after rotations kills the
  session as a whole.
- Tokens are carried in the body, not cookies: there is no browser client, hence no CSRF surface and no
  cookie flags to get wrong.

**Consequences:** A client that loses a refresh response and retries loses the session (strict reuse
detection, ADR-0016). Logout does not invalidate access tokens already issued, which stay usable for up
to their 15-minute lifetime. If a browser frontend is ever added, cookie transport and CSRF protection
need their own decision.

---

## ADR-0019 — Authenticated requests and the authorization policy

**Status:** Accepted (Milestone 3)

**Context:** Every later endpoint must establish who is calling and which data they may touch, in one
way that is hard to get wrong.

**Decision:**
- Bearer access tokens only (`Authorization: Bearer`). `CurrentUser` is a FastAPI dependency that
  verifies the token (ADR-0015) and loads the user from the database on every request, so deleting an
  account takes effect immediately instead of at token expiry. The cost is one indexed read per request.
- Failure codes, all `401` with `WWW-Authenticate: Bearer`: `not_authenticated` (no credentials),
  `token_expired` (a well-formed token past its expiry, so the client knows to refresh) and
  `invalid_token` (everything else).
- **Policy:** the caller's identity comes only from the verified token. Routes pass `user.id` to the
  repositories as the owner; no path, query, body or header value is ever used to select the owner.
  Repositories filter on the owner inside the query (ADR-0014), so another user's resource is a `404`
  indistinguishable from a missing one, and a user's listings contain only their own records.
- `GET /auth/me` returns id, email and creation time, never the password hash, with `no-store`.

**Consequences:** There is no role system and no admin: authorization is purely "owner only". If roles
are ever needed, the dependency is the single place to extend. The per-request user lookup is the first
thing to cache if it ever shows up in measurements (it would reintroduce a revocation delay). Access
tokens already issued survive logout until they expire (at most 15 minutes), pinned by a test so a
change is deliberate. API-level cross-user tests for real resources are written with the document
endpoints in Milestone 4, following the pattern proven here.

---

## ADR-0020 — Storage protocol and the local adapter

**Status:** Accepted (Milestone 4)

**Context:** Uploaded files are untrusted and must survive container recreation; the backend will move
to S3 later without changing domain code.

**Decision:**
- `Storage` is a protocol with `put(key, BinaryIO) -> size`, `delete(key) -> bool` and `healthy()`. It is
  one of the few real boundaries in the system (the other planned ones are search and LLM providers).
  Reading is added with the worker, which is its first consumer.
- Keys are generated by the server (`<2 hex>/<32 hex>`, 128 random bits) and validated with a strict
  full-match pattern by every operation. No client-supplied string (filename, MIME, id) is ever part of a
  key, so path traversal is impossible by construction.
- `LocalStorage` additionally requires every directory it writes into to resolve inside the root, so a
  symlink planted in the tree cannot redirect writes. The comparison is done on directories that
  already exist: resolving not-yet-created paths is unreliable on Windows and made an earlier version of
  this check fail intermittently under concurrency (found by a stress run, 2 in 1,200 calls).
- Objects are written to a temporary file in the same filesystem, fsynced, then published with a hard
  link: atomic, and it fails rather than overwrite (`StorageKeyExistsError`). Failure leaves nothing.
- Directories are `0700` and files `0600`. Blocking I/O runs in a worker thread.
- The Dockerfile creates `/data/uploads` owned by the runtime user so a new named volume mounted there is
  initialised with that owner; the container root filesystem stays read-only.
- Readiness gains a `storage` check: the root can be created and written.

**Consequences:** Objects have no readable names, so operators cannot browse the volume by filename;
the database maps ids to keys. The file and its database record are not transactional; orphans are
possible and are reconciled later. Uploaded content sits unencrypted on the volume (encryption at rest is
a property of the host or of S3 in the cloud deployment).

---

## ADR-0021 — Upload validation: content decides, the client does not

**Status:** Accepted (Milestone 4). The no-external-library choice was made by default when the author
did not answer; revisit if more formats are added.

**Context:** Uploaded files are untrusted. The file name and the declared MIME type are chosen by the
sender, so neither can be a basis for acceptance.

**Decision:**
- Three formats are accepted, identified only from the bytes:
  - **PDF**: starts with `%PDF-`.
  - **DOCX**: a readable ZIP containing `[Content_Types].xml` and `word/document.xml`, with at most
    10,000 entries and 256 MiB total uncompressed size (read from the central directory, nothing is
    extracted), so a tiny archive cannot promise an unbounded expansion to the extractor.
  - **TXT**: strict UTF-8 with no control characters other than tab, newline and carriage return and no
    DEL. UTF-16, Latin-1 and binary data are refused rather than guessed at.
- The canonical content type is derived from the detection and stored; the declared one is ignored. A
  file named `invoice.pdf` that is plain text is stored as text, and a PNG renamed `.txt` is refused.
- The original file name is sanitized for display only: last path component, control and
  format characters (including bidirectional overrides) removed, reserved characters replaced, trailing
  dots and spaces stripped, 255 characters at most with the extension preserved. It never participates in
  a storage key or path.
- Intake streams the upload through a temporary file (memory up to 1 MiB, then disk), counting bytes and
  computing SHA-256 off the event loop, and stops reading as soon as the limit is exceeded. Empty files
  are refused. Detection runs after the full content is available because DOCX validity needs the ZIP
  central directory at the end of the file.
- A passing check means "plausibly this format". Extraction (Milestone 5) must still treat the content
  as hostile.

**Consequences:** Rejection reasons are specific (too large, empty, unsupported type) and safe to show.
Detection is deliberately conservative: a valid-but-unusual file (UTF-16 text, a DOCX written by an
unusual tool that omits a standard part) is refused. A PDF is accepted on its header alone, so a
corrupt PDF is accepted and fails later at extraction, which is reported as a failed document.
Without a size guard at the HTTP layer the framework would buffer a whole request before intake sees
it; that guard is part of the next step.

---

## ADR-0022 — Upload endpoint: size guard at the edge, ordered steps, compensation

**Status:** Accepted (Milestone 4)

**Context:** Multipart parsing in the framework copies the whole request to a temporary file before a
route can look at it, so a limit enforced only inside the route would let an oversized body be fully
received first. Files and database records live in separate systems with no shared transaction.

**Decision:**
- A pure ASGI middleware enforces body size before the application reads anything. It refuses on a
  declared `Content-Length` over the limit without reading a byte, and for bodies without one counts
  bytes and aborts at the limit. The upload route may carry `MAX_UPLOAD_SIZE_MB` plus 64 KiB of multipart
  framing; every other route is capped at 1 MiB. Intake then enforces the exact file limit.
- `POST /api/v1/documents` answers `202 Accepted` with the document in state `pending`. Processing arrives
  in Milestone 5; until then documents remain `pending`. Choosing `202` now avoids changing the contract
  later.
- Order: validate and hash, refuse a duplicate (same owner, same content hash) before touching storage,
  store the file under a new key, create the record. If creating the record fails, including by losing a
  race on the unique `(owner_id, sha256)` index, the stored file is removed and the original error (or a
  `409` pointing at the winner) is reported. A failed cleanup is logged and never masks the cause.
- Status mapping: `413` too large, `415` unsupported content, `400` empty, `409` duplicate (with a
  `Location` header for the existing document, which the caller owns), `503` file store unavailable with no
  paths or driver text in the body.
- The owner is the authenticated user only; the response never contains the storage key or any path.

**Consequences:** A crash between storing the file and creating the record leaves an unreferenced file
(never a record without a file); reconciliation in Milestone 5 removes such orphans. Concurrency is safe
but a burst of identical uploads each spends a temporary copy before the duplicate is detected. Until the
rate limiting of Milestone 7 exists, authenticated users can upload repeatedly up to the size limit.

---

## ADR-0023 — Reading and deleting documents: id errors, not-found and cleanup order

**Status:** Accepted (Milestone 4). Settles the question left open in Milestone 2 (404 vs 422 for ids).

**Context:** Document routes take an id from the path. The API must neither reveal whether someone else's
document exists nor leave data behind when a document is deleted.

**Decision:**
- A **malformed** id (not 24 hexadecimal characters) is `422 validation_error`: it is a client mistake,
  reveals nothing about stored data, and distinguishing it keeps client bugs visible.
- A **well-formed** id that does not exist and one that belongs to another user produce the same
  `404 document_not_found` with the same body, from a single code path. The test suite pins the exact body
  so wording cannot drift into a difference.
- Listing is `limit` (1-100, default 20) and `offset` (>= 0), newest first, with the owner's `total`. The
  owner is always the authenticated user; owner-like query parameters and headers are ignored (tested).
- Deletion removes the database record first, then the chunks, then the stored file. The record is the
  source of truth, so once it is gone the document is gone for the user even if a later cleanup step fails;
  failures there are logged and left for reconciliation, and the call still returns `204`. Two concurrent
  deletes yield exactly one `204` and `404` for the rest.
- Deleting frees the content hash, so the same file can be uploaded again.

**Consequences:** A failed chunk or file cleanup leaves unreferenced data (never a visible document) until
reconciliation exists in Milestone 5. A `204` therefore means "the document is deleted", not "every byte is
gone". Offset pagination can skip or repeat items if documents are added between pages; acceptable for
per-user lists (ADR-0014).

