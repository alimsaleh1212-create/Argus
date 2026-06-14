# Sentinel — developer task shortcuts.
# Hides the `-c config/alembic.ini` flag and standardises common commands.

.PHONY: help install up down logs migrate downgrade test test-unit test-integration test-e2e test-smoke cov lint fmt

help:
	@echo "install           uv sync (pinned deps)"
	@echo "up / down         docker compose up -d --wait / down -v"
	@echo "migrate           alembic upgrade head"
	@echo "downgrade         alembic downgrade base"
	@echo "test              memory-safe default: batched unit tier (no Docker)"
	@echo "test-unit         unit tier, batched per-file (won't OOM)"
	@echo "test-integration  testcontainers tier, batched per-file (Docker)"
	@echo "test-e2e          in-process e2e tier, batched (no Docker)"
	@echo "test-smoke        true e2e against a running compose stack (needs_compose)"
	@echo "cov               combined coverage gate (unit+integration), batched, fail <80%"
	@echo "lint / fmt        ruff check / ruff format + import-linter"

install:
	uv sync

up:
	docker compose up -d --wait

down:
	docker compose down -v

logs:
	docker compose logs -f api worker

migrate:
	uv run alembic -c config/alembic.ini upgrade head

downgrade:
	uv run alembic -c config/alembic.ini downgrade base

# Default test target = batched unit tier. Each file runs in its own pytest
# subprocess so heavy imports (spaCy/Presidio, graphiti) are reclaimed between
# files — peak memory ≈ one file, so it never OOMs (even with the stack up).
test: test-unit

test-unit:
	BATCH=6 scripts/run-tests.sh unit

test-integration:
	scripts/run-tests.sh integration

test-e2e:
	BATCH=2 scripts/run-tests.sh e2e

test-smoke:
	uv run pytest tests/e2e -m needs_compose

# Combined coverage gate, collected the memory-safe (batched) way.
cov:
	rm -f .coverage .coverage.*
	COV=1 BATCH=6 scripts/run-tests.sh unit
	COV=1 scripts/run-tests.sh integration
	uv run coverage report --fail-under=80

lint:
	uv run ruff check .
	uv run lint-imports

fmt:
	uv run ruff format .
	uv run ruff check --fix .
