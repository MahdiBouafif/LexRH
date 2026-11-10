.PHONY: install lint format typecheck test test-unit up down migrate ingest-dry ingest embed-dry embed search eval-retrieval eval-rerank

# Usage: make ingest-dry DATA=data/raw/LEGITEXT000006072050
ingest-dry:
	uv run python -m lexrh.rag.ingest $(DATA) --dry-run

# Usage: make search Q="préavis de licenciement" DATE=2020-01-01
eval-retrieval:
	uv run python -m lexrh.eval.retrieval

eval-rerank:
	uv run python -m lexrh.eval.retrieval --rerank

search:
	uv run python -m lexrh.rag.search "$(Q)" $(if $(DATE),--date $(DATE),)

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

.PHONY: serve
serve: ## Run the API locally on :8000
	uv run uvicorn lexrh.api.main:app --port 8000

.PHONY: ask
ask: ## Ask a question: make ask Q="..." DATE=2020-01-01
	uv run python -m lexrh.rag.answer "$(Q)" $(if $(DATE),--date $(DATE),)

.PHONY: eval-answers
eval-answers: ## End-to-end /ask evaluation (needs the generator settings in .env)
	uv run python -m lexrh.eval.answers --pause 2 --out evals/last_answers_report.md
