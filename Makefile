# Developer commands. Requires uv (https://docs.astral.sh/uv/) and GNU Make.
# Targets for running the stack (up/down/logs/health) arrive with Docker Compose (Milestone 1/10).

.DEFAULT_GOAL := help
UV_RUN := uv run --locked

.PHONY: help install run test lint format format-check typecheck check

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Create the virtualenv and install locked dependencies
	uv sync --locked

run: ## Run the API locally with auto-reload on http://127.0.0.1:8000
	$(UV_RUN) uvicorn docusearch.main:create_app --factory --reload --host 127.0.0.1 --port 8000

test: ## Run the unit test suite with coverage
	$(UV_RUN) pytest -m "not integration" --cov --cov-report=term-missing

lint: ## Lint with Ruff
	$(UV_RUN) ruff check .

format: ## Format code with Ruff
	$(UV_RUN) ruff format .

format-check: ## Check formatting without changing files
	$(UV_RUN) ruff format --check .

typecheck: ## Static type checking with mypy
	$(UV_RUN) mypy

check: lint format-check typecheck test ## Run every static check and the tests
