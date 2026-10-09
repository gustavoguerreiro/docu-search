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
