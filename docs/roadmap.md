# DocuSearch — Roadmap

Work proceeds **one small, reviewable task per session**. A milestone may span several sessions.
Status below reflects verified state of the repository, not intentions.

Legend: ⬜ not started · 🟨 in progress · ✅ done (verified)

| # | Milestone | Status |
|---|---|---|
| 0 | Repository assessment and project plan | ✅ |
| 1 | Project foundation | ✅ |
| 2 | MongoDB infrastructure and persistence | ✅ |
| 3 | Authentication and authorization | ✅ |
| 4 | Document upload and storage | ✅ |
| 5 | Asynchronous ingestion pipeline | 🟨 |
| 6 | Text search | ⬜ |
| 7 | Production-minded API hardening | ⬜ |
| 8 | Observability (Prometheus + Grafana) | ⬜ |
| 9 | Optional LLM integration | ⬜ |
| 10 | Docker Compose hardening and DX | ⬜ |
| 11 | Terraform / AWS preparation (no provisioning) | ⬜ |
| 12 | Final quality and portfolio presentation | ⬜ |

## Milestone 0 — Repository assessment and project plan ✅

Repository was empty (no commits). Created `README.md`, `CLAUDE.md`, `.gitignore`,
`.env.example`, `docs/architecture.md`, `docs/roadmap.md`, `docs/decisions.md`.

Acceptance: documents exist, are mutually consistent, contain no secrets, and make clear that
nothing is implemented yet. Nothing was installed or executed beyond Git inspection.

## Milestone 1 — Project foundation ✅

Suggested sub-steps (one per session):

1. ✅ Python project config (`pyproject.toml`), pinned dependencies, Ruff + mypy config, Makefile skeleton. (ADR-0008; `make check` passes locally. Not yet exercised in CI or Docker.)
2. ✅ Pydantic Settings (`APP_ENV`, `LOG_LEVEL` only; later milestones add their fields) + structured JSON logging with key-based secret redaction. Wiring into the app/uvicorn happens in step 3.
3. ✅ FastAPI app factory (`create_app`, run with `uvicorn --factory`) + `/api/v1/health/live` and `/ready` + unit tests. Readiness runs the checks registered on `app.state.readiness_checks`; none are registered until Mongo/Redis exist. uvicorn's own loggers are routed to the JSON formatter once the lifespan starts (its two earliest startup lines stay plain text).
4. ✅ Dockerfile (multi-stage, non-root, one image for API and later worker) + Docker Compose with `api`, MongoDB 8.0 and Redis 7.4 (pinned, health-checked, named volumes, no host ports for Mongo/Redis). ADR-0009. Verified locally with Docker only; CI build comes in step 5. The worker service is added in Milestone 5.
5. ✅ GitHub Actions CI (`.github/workflows/ci.yml`): `python` job (ruff, format check, mypy, pytest) and `docker` job (compose validation, image build, stack up with health checks, live/ready calls); `.gitattributes` enforces LF. ADR-0010. **Not yet run on GitHub** — validated with actionlint and by running the same commands locally and in a clean Linux container; the first real run happens when this is pushed.
6. ✅ README getting-started, verified end to end from a fresh `git clone` of the committed repo (Option A: install, check, run + curl; Option B: up, health, logs, down; `.env` step). Tested on Windows 11 with Git Bash only; the CI run on Linux/GitHub is still pending the first push.

Acceptance: app starts locally; health endpoints work; config from env vars; tests/lint/types
pass; no real credentials needed; README explains setup. All verified locally (see step 6). Remaining
caveat: the GitHub Actions workflow has not run on GitHub yet.

## Milestone 2 — MongoDB infrastructure and persistence ✅

Sub-steps (one per session):

1. ✅ Mongo settings, client lifecycle in the app lifespan (`tz_aware=True`, bounded timeouts), real
   `mongodb` readiness check. ADR-0011. Verified against the Compose MongoDB: ready 200 → 503 when
   stopped (2 s) → 200 after restart; `/live` unaffected. CI asserts the check is `ok`.
2. ✅ Integration-test infrastructure (ADR-0012): throwaway `mongo-test` service in the Compose `test`
   profile (loopback `127.0.0.1:27018`, tmpfs data), `make test-integration`, unique database per test,
   `integration` CI job. First tests: ping, UTC-aware round trip, client closed by the lifespan, `/ready`
   200/503 through the real lifecycle. Verified locally; the new CI job has not run on GitHub yet.
3. ✅ `core.clock.utc_now()` (aware UTC, millisecond-truncated so values survive a MongoDB round trip unchanged) and `db.ids` (`parse_object_id`, `object_id_to_str`, `InvalidIdError`, pydantic `ObjectIdStr` → 422 on malformed ids). Unit + integration tests. Policy for unknown-vs-malformed ids (404 vs 422) is decided with the document endpoints.
4. ✅ Index definitions, idempotent `ensure_indexes`, `verify_indexes`, and `IndexInitializer` run at startup (ADR-0013). Collections are created implicitly by index creation. `/ready` gains an `indexes` check that retries, so a process started while MongoDB was down repairs itself; readiness checks now run concurrently. Verified against a real MongoDB incl. query plans (`explain`). Text index deferred to Milestone 6.
5. ✅ Repositories (`db/repositories/`: users, documents, chunks, refresh tokens) and record models
   (`db/models.py`), limited to operations milestones 3–6 explicitly need. Ownership is inside every
   document/chunk query; email normalization is centralized; refresh-token revocation is an atomic
   single-use guard. Listing index changed to `(owner_id, created_at, _id)` (ADR-0014) with a
   retired-index drop. Verified against real MongoDB, incl. concurrent registration and revocation,
   and on an existing dev volume.

Acceptance (all met, verified locally): connections opened and closed correctly (lifespan, tested);
required indexes created and verified (`verify_indexes`, `explain()` plans); integration tests run
against a real MongoDB (`make test-integration`); repositories are thin classes over the driver with
no interfaces or generic base class. Not verifiable from the development environment: the CI changes made in this
milestone (`integration` job, `mongodb`/`indexes` readiness assertion) running on GitHub.

## Milestone 3 — Authentication and authorization ✅

Sub-steps (one per session):

1. ✅ Auth primitives, no endpoints: Argon2id hashing off the event loop (`core/passwords.py`), HS256
   access tokens with strict verification (`core/tokens.py`), JWT settings (`JWT_SECRET_KEY` required
   in production, random per process otherwise). ADR-0015. Mutation-checked: weakening audience,
   required-claims, algorithm or subject validation fails the tests.
2. ✅ Refresh tokens (`auth/refresh_tokens.py`, ADR-0016): opaque 256-bit token stored as SHA-256,
   rotation per session family, reuse revokes the family, absolute session lifetime,
   `REFRESH_TOKEN_TTL_DAYS`, `family_id` index. Verified against real MongoDB, incl. concurrent rotation
   and 5 mutation checks. Chosen by default (option A) when the author did not pick; easy to relax.
3. ✅ `AuthService` + `POST /auth/register` (uniform `202`) and `POST /auth/login`; password policy
   (12-128 chars); dummy-hash timing equalization; common error body (`api/errors.py`, validation
   errors never echo values). ADR-0017. Verified end to end against the Docker stack, incl. a log-leak
   check, and mutation-checked.
4. ✅ `POST /auth/refresh` (single use, rotation, reuse revokes the session, missing user ends
   sessions) and `POST /auth/logout` (always `204`, idempotent). Refresh token travels in the JSON body.
   Verified end to end against the Docker stack and with concurrent refreshes.
5. ✅ `CurrentUser` dependency (`api/dependencies.py`), `GET /auth/me`, explicit authorization policy
   (owner = authenticated principal only), deleted accounts lose access immediately. ADR-0019.
   Cross-user tests at API level use a throwaway route with the real dependency and repository;
   real document endpoints (and their cross-user tests) arrive in Milestone 4.

Acceptance (all met, verified locally): authentication tests pass (108 integration + unit); invalid,
expired, revoked and malformed tokens are rejected; cross-user access tests pass; secrets are not
logged (checked against the container logs). Not done in this milestone, by design: rate limiting and
account lockout (Milestone 7), access-token revocation, email verification and password reset.

## Milestone 4 — Document upload and storage ✅

Defaults adopted when the author did not answer: content sniffing by magic bytes with no external
library, 25 MB upload limit (configurable 1-100), and `POST /documents` answers `202` from the start so
the contract does not change when processing arrives in Milestone 5 (until then documents stay `pending`).

Sub-steps (one commit each):

1. ✅ `Storage` protocol + `LocalStorage` (server-generated keys, atomic no-overwrite publish, containment
   check, off-loop I/O), `storage` readiness check, `uploads_data` volume. ADR-0020. Verified on Linux
   (36 tests, 6 mutations) and in the container (non-root write, persistence across recreation).
2. ✅ Upload validation (`documents/`, ADR-0021): content-based type detection (PDF / DOCX / UTF-8
   TXT), filename sanitization, streaming intake with size limit and SHA-256. Pure functions, no
   endpoint yet. 70 unit tests; 12 mutations of the rules all caught.
3. ✅ Request-size guard (`api/limits.py`) and `POST /api/v1/documents` (`202`, duplicate detection with
   `409` + `Location`, file removed if the record cannot be created). ADR-0022. Verified end to end
   against the Docker stack (oversized upload rejected in milliseconds with 0 bytes read) and 8
   mutations caught.
4. ✅ `GET /documents` (paginated, owner-scoped), `GET /documents/{id}`, `DELETE /documents/{id}`;
   cross-user API tests; malformed id = `422`, unknown or foreign id = identical `404` (ADR-0023).
   Verified on the Docker stack including container recreation, plus 8 mutations caught.

Acceptance (all met, verified locally): valid files are stored and tracked; invalid files rejected;
users cannot access or delete others' files (API-level matrix incl. forged owner hints); storage
operations have meaningful tests (Linux-only security tests run in a container); files persist across
container recreation with the documented volume. Not done here, by design: processing (Milestone 5), the
`/content` and `/retry` endpoints, rate limiting (Milestone 7), reconciliation of orphaned files
(Milestone 5), and an S3 adapter (Milestone 11).

## Milestone 5 — Asynchronous ingestion pipeline 🟨

Decisions made by the author: `pypdf` for PDF and `python-docx` for DOCX; chunking with a real tokenizer
(`tiktoken`, `cl100k_base`, exact counts for that tokenizer). Delegated to the assistant and recorded in
ADR-0024: the worker uses the async data layer through `asyncio.run` per job.

Sub-steps (one session each):

1. ✅ Queue and worker foundation: Redis settings and client, `JobQueue` boundary + RQ implementation,
   `redis` readiness check, `python -m docusearch.worker` (same image, own healthcheck), `worker` Compose
   service, `redis-test` for integration tests. The job is a placeholder. ADR-0024. Verified on Linux
   (real worker process, clean SIGTERM) and in the Compose stack.
2. ⬜ Extraction: `pypdf` (text and page numbers), `python-docx`, UTF-8 text; normalization; limits
   against hostile input (pages, extracted size); pure functions.
3. ⬜ Chunking with `tiktoken` `cl100k_base`: ~800 tokens, ~100 overlap, both configurable; page
   numbers carried to chunks; vocabulary baked into the image (no runtime network).
4. ⬜ Processing job: atomic claim, state transitions (`pending -> processing -> completed | failed`),
   idempotent chunk writes, transient vs permanent failures, bounded retries, safe error codes; upload
   enqueues and returns `202` only after enqueueing.
5. ⬜ Recovery: reconcile documents stuck in `pending`/`processing`, enqueue failures, orphaned files.
6. ⬜ End to end and failure-scenario tests; documentation.

Acceptance: an uploaded file moves through the full lifecycle; processing is independent of the HTTP
request; failures are observable; retrying is safe; integration tests cover success and failure.

## Milestones 6–12

Scope and acceptance criteria for each are defined in the project instructions provided by the
author and will be expanded into sub-steps here when the milestone begins. Summary of intent:

- **6** Mongo text search scoped to owner and completed documents, pagination, document endpoints.
- **7** Error schema, correlation IDs, rate limiting, timeouts, graceful shutdown.
- **8** Metrics, Prometheus config, provisioned Grafana dashboards, alert rules.
- **9** Optional, disabled-by-default LLM provider abstraction and Q&A endpoint with real chunk references.
- **10** Compose hardening: health checks, non-root, no unnecessary host exposure, `make` targets.
- **11** Terraform modules and scripts for AWS — validated locally only, never applied.
- **12** Review pass and professional README, without invented benchmarks or deployments.

## Hard constraints (all milestones)

- No `terraform apply`/`destroy`, no AWS resources, no ECR pushes, no cloud costs.
- No paid external APIs in tests; LLM off by default.
- No secrets in Git, images or logs.
- No commit or push without explicit instruction.
