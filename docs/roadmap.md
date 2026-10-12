# DocuSearch — Roadmap

Work proceeds **one small, reviewable task per session**. A milestone may span several sessions.
Status below reflects verified state of the repository, not intentions.

Legend: ⬜ not started · 🟨 in progress · ✅ done (verified)

| # | Milestone | Status |
|---|---|---|
| 0 | Repository assessment and project plan | ✅ |
| 1 | Project foundation | ✅ |
| 2 | MongoDB infrastructure and persistence | ✅ |
| 3 | Authentication and authorization | 🟨 |
| 4 | Document upload and storage | ⬜ |
| 5 | Asynchronous ingestion pipeline | ⬜ |
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

## Milestone 3 — Authentication and authorization 🟨

Sub-steps (one per session):

1. ✅ Auth primitives, no endpoints: Argon2id hashing off the event loop (`core/passwords.py`), HS256
   access tokens with strict verification (`core/tokens.py`), JWT settings (`JWT_SECRET_KEY` required
   in production, random per process otherwise). ADR-0015. Mutation-checked: weakening audience,
   required-claims, algorithm or subject validation fails the tests.
2. ✅ Refresh tokens (`auth/refresh_tokens.py`, ADR-0016): opaque 256-bit token stored as SHA-256,
   rotation per session family, reuse revokes the family, absolute session lifetime,
   `REFRESH_TOKEN_TTL_DAYS`, `family_id` index. Verified against real MongoDB, incl. concurrent rotation
   and 5 mutation checks. Chosen by default (option A) when the author did not pick; easy to relax.
3. ⬜ Auth service + `POST /auth/register` and `POST /auth/login`: email normalization, no user
   enumeration (uniform responses, dummy-hash timing), password policy, consistent error body.
4. ⬜ `POST /auth/refresh` and `POST /auth/logout` (revocation).
5. ⬜ Authentication dependency, `GET /auth/me`, ownership policy helper, cross-user tests.

Acceptance: authentication tests pass; invalid, expired, revoked and malformed tokens are rejected;
cross-user access tests pass; secrets are not logged.

## Milestones 4–12

Scope and acceptance criteria for each are defined in the project instructions provided by the
author and will be expanded into sub-steps here when the milestone begins. Summary of intent:

- **4** Local storage adapter behind an interface, upload validation (content sniffing, size limit, allowlist), SHA-256, per-user duplicate detection.
- **5** Redis/RQ worker, extraction, chunking, state machine, idempotency, retries, stale-job reconciliation; upload returns 202.
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
