SHELL := /bin/bash
WEB := cd apps/web && pnpm

.PHONY: help setup up down load-data dev web test eval eval-slow check format

# Pin docker to the local engine, whatever `docker context` is current on this machine.
export DOCKER_HOST ?= unix:///var/run/docker.sock

help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Install Python and web dependencies
	uv sync
	$(WEB) install --frozen-lockfile

up: ## Start the helpdesk's Postgres (pgvector) on localhost:5433
	docker compose up -d --wait postgres

down: ## Stop it (data is kept)
	docker compose down

load-data: ## Load colleges, offices and documents (only changed documents are re-embedded)
	uv run python -m apps.api.cli load-data

dev: ## Run the helpdesk API on :8100 (CampusERP forwards /api/helpdesk/* here)
	uv run uvicorn apps.api.main:app --reload --port 8100

web: ## Run the public chat page on :5173
	$(WEB) dev

test: ## Tests (CampusERP-backed ones skip if its API is not on :8000)
	uv run pytest -q

eval: ## Fast eval: retrieval and scope leaks, no LLM calls
	uv run python -m evals.run --tier fast

eval-slow: ## Slow eval: full answers, judge and tools against CampusERP
	uv run python -m evals.run --tier slow

check: ## Lint, format check and web build
	uv run ruff check .
	uv run ruff format --check .
	$(WEB) build

format: ## Auto-format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .
