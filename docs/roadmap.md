# DocuSearch — Roadmap

Work proceeds **one small, reviewable task per session**. A milestone may span several sessions.
Status below reflects verified state of the repository, not intentions.

Legend: ⬜ not started · 🟨 in progress · ✅ done (verified)

| # | Milestone | Status |
|---|---|---|
| 0 | Repository assessment and project plan | ✅ |
| 1 | Project foundation | ⬜ |
| 2 | MongoDB infrastructure and persistence | ⬜ |
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

## Milestone 1 — Project foundation (next)

Suggested sub-steps (one per session):

1. ⬜ Python project config (`pyproject.toml`), pinned dependencies, Ruff + mypy config, Makefile skeleton.
2. ⬜ Pydantic Settings + structured JSON logging.
3. ⬜ FastAPI app factory + `/api/v1/health/live` and `/ready` (readiness stubbed until Mongo/Redis exist) + unit tests.
4. ⬜ Dockerfile + Docker Compose (MongoDB, Redis) with pinned images.
5. ⬜ GitHub Actions CI (lint, types, tests).
6. ⬜ README getting-started verified end to end.

Acceptance: app starts locally; health endpoints work; config from env vars; tests/lint/types
pass; no real credentials needed; README explains setup.

## Milestones 2–12

Scope and acceptance criteria for each are defined in the project instructions provided by the
author and will be expanded into sub-steps here when the milestone begins. Summary of intent:

- **2** Mongo client lifecycle, repositories, index initialization, UTC timestamps, ObjectId handling, integration tests against real MongoDB.
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
