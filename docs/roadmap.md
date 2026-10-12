# DocuSearch — Roadmap

Work proceeds **one small, reviewable task per session**. A milestone may span several sessions.
Status below reflects verified state of the repository, not intentions.

Legend: ⬜ not started · 🟨 in progress · ✅ done (verified)

| # | Milestone | Status |
|---|---|---|
| 0 | Repository assessment and project plan | ✅ |
| 1 | Project foundation | ✅ |
| 2 | MongoDB infrastructure and persistence | 🟨 |
| 3 | Authentication and authorization | ⬜ |
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

## Milestone 2 — MongoDB infrastructure and persistence 🟨

Sub-steps (one per session):

1. ✅ Mongo settings, client lifecycle in the app lifespan (`tz_aware=True`, bounded timeouts), real
   `mongodb` readiness check. ADR-0011. Verified against the Compose MongoDB: ready 200 → 503 when
   stopped (2 s) → 200 after restart; `/live` unaffected. CI asserts the check is `ok`.
2. ⬜ Integration-test infrastructure: real MongoDB for tests locally and in CI (decide how tests reach
   MongoDB; the Compose instance has no host port). First integration tests: client lifecycle, ping.
3. ⬜ UTC-aware timestamp helper and ObjectId conversion/validation helpers.
4. ⬜ Collection/index initialization (`users`, `documents`, `chunks`, `refresh_tokens`) with index
   verification, run at startup.
5. ⬜ Minimal repository modules for the initial collections.

Acceptance: connections opened and closed correctly; required indexes created and verified;
integration tests run against a real MongoDB; database operations not hidden behind excessive
abstractions.

## Milestones 3–12

Scope and acceptance criteria for each are defined in the project instructions provided by the
author and will be expanded into sub-steps here when the milestone begins. Summary of intent:

- **3** Register/login/refresh/logout/me, JWT, refresh rotation with hashed tokens, ownership policies, cross-user tests.
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
