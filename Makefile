.PHONY: install lint format typecheck test test-unit up down migrate

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test-unit:
	uv run pytest -m "not integration"

test:
	uv run pytest

up:
	docker compose up --build -d

down:
	docker compose down

migrate:
	uv run alembic upgrade head
