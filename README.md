# DocuSearch

> **Status: planning stage (Milestone 0).** No application code exists yet. This README describes
> the intended project; sections marked *planned* are not implemented. See
> [`docs/roadmap.md`](docs/roadmap.md) for real progress.

DocuSearch is a document ingestion, indexing and search platform exposed as a REST API. Users
register, upload documents (PDF, DOCX, TXT), track processing status, and search their content.
Later stages add optional hybrid search and LLM-powered question answering with references to
source chunks.

It is a personal portfolio project built to practice and demonstrate backend engineering with
MongoDB, asynchronous processing, application security, observability, Docker and
infrastructure-as-code.

## Planned architecture

A modular monolith: a FastAPI API plus a separately executed RQ worker, sharing one Docker image.
MongoDB stores users, document metadata and text chunks; Redis/RQ runs background processing;
uploaded files go through a storage abstraction (local filesystem first, S3 later); Prometheus and
Grafana provide observability.

Details: [`docs/architecture.md`](docs/architecture.md). Decisions: [`docs/decisions.md`](docs/decisions.md).

## Planned technology

Python 3.13 · FastAPI · Pydantic · PyMongo Async · MongoDB · Redis + RQ · Docker Compose ·
Prometheus · Grafana · pytest · Ruff · mypy · JWT + Argon2id · Terraform · GitHub Actions.

## Getting started

*Not available yet.* Local setup instructions arrive with Milestone 1. Configuration will be
read from environment variables; [`.env.example`](.env.example) is the template and no real
credentials will be required.

## AWS

Terraform definitions for a future AWS deployment (ECS Fargate, ECR, S3, MongoDB Atlas, managed
Redis, Secrets Manager, CloudWatch, ALB) are a *planned* deliverable. **No AWS resources have been
or will be created during development.**

## Repository layout

```
CLAUDE.md          instructions for AI-assisted sessions
README.md
.env.example       configuration template (planning stage)
docs/
  architecture.md
  roadmap.md
  decisions.md
```
