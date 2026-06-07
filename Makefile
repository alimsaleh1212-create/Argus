# Sentinel — developer task shortcuts.
# Hides the `-c config/alembic.ini` flag and standardises common commands.

.PHONY: help install up down logs migrate downgrade test test-unit test-integration test-e2e lint fmt

help:
	@echo "install           uv sync (pinned deps)"
	@echo "up / down         docker compose up -d --wait / down -v"
	@echo "migrate           alembic upgrade head"
	@echo "downgrade         alembic downgrade base"
	@echo "test              full pytest suite"
	@echo "test-unit         unit tier only"
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

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

test-e2e:
	uv run pytest tests/e2e

lint:
	uv run ruff check .
	uv run lint-imports

fmt:
	uv run ruff format .
	uv run ruff check --fix .
