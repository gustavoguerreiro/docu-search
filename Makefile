# Developer commands. Requires uv (https://docs.astral.sh/uv/) and GNU Make.
# `up`/`down`/`logs`/`health` need Docker with the Compose plugin.

.DEFAULT_GOAL := help
UV_RUN := uv run --locked

.PHONY: help install run up down logs health test test-integration test-db-up test-db-down lint format format-check typecheck check

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Create the virtualenv and install locked dependencies
	uv sync --locked

run: ## Run the API locally with auto-reload on http://127.0.0.1:8000
	$(UV_RUN) uvicorn docusearch.main:create_app --factory --reload --host 127.0.0.1 --port 8000

up: ## Build and start the Docker stack (waits until services are healthy)
	docker compose up -d --build --wait

down: ## Stop the Docker stack (incl. the test database); named volumes (data) are kept
	docker compose --profile "*" down

logs: ## Follow logs of the Docker stack
	docker compose logs -f --tail=100

health: ## Call the liveness and readiness endpoints of the running stack
	curl -fsS http://127.0.0.1:8000/api/v1/health/live
	@echo
	curl -fsS http://127.0.0.1:8000/api/v1/health/ready
	@echo

test: ## Run the unit test suite with coverage
	$(UV_RUN) pytest -m "not integration" --cov --cov-report=term-missing

test-db-up: ## Start the throwaway MongoDB used by integration tests (127.0.0.1:27018)
	docker compose --profile test up -d --wait mongo-test

test-db-down: ## Remove the throwaway test MongoDB (its data is ephemeral)
	docker compose --profile test rm -sf mongo-test

test-integration: ## Run integration tests against a throwaway MongoDB (always cleaned up)
	$(MAKE) test-db-up
	$(UV_RUN) pytest -m integration; rc=$$?; $(MAKE) test-db-down; exit $$rc

lint: ## Lint with Ruff
	$(UV_RUN) ruff check .

format: ## Format code with Ruff
	$(UV_RUN) ruff format .

format-check: ## Check formatting without changing files
	$(UV_RUN) ruff format --check .

typecheck: ## Static type checking with mypy
	$(UV_RUN) mypy

check: lint format-check typecheck test ## Run every static check and the tests
