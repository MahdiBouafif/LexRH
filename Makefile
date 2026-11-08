.PHONY: install lint format typecheck test test-unit up down migrate ingest-dry ingest embed-dry embed

# Usage: make ingest-dry DATA=data/raw/LEGITEXT000006072050
ingest-dry:
	uv run python -m lexrh.rag.ingest $(DATA) --dry-run

embed-dry:
	uv run python -m lexrh.rag.embed_chunks --dry-run

embed:
	uv run python -m lexrh.rag.embed_chunks

ingest:
	uv run python -m lexrh.rag.ingest $(DATA)

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
