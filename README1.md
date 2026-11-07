# LexRH

HR case copilot for French SMEs: date-aware legal RAG with verified citations, MCP tools, and a human-approved agent. See [docs/SCOPE.md](docs/SCOPE.md).

> Status: week 1 (skeleton). Synthetic data only, not legal advice.

## Quick start

```bash
cp .env.example .env          # then edit the values
make install                  # uv sync
make up                       # Postgres (pgvector) + API on :8000
curl localhost:8000/health
curl localhost:8000/ready
```

## Development

```bash
make lint        # ruff
make typecheck   # mypy (strict)
make test-unit   # no database needed
make test        # includes integration tests (Docker or TEST_DATABASE_URL)
make migrate     # alembic upgrade head against DATABASE_URL
```

Integration tests use `TEST_DATABASE_URL` if set (this is what CI does), otherwise start a throwaway `pgvector/pgvector:pg16` container through testcontainers, otherwise they skip.

## Layout

```
src/lexrh/api/     FastAPI app (health, readiness; query endpoints from week 4)
src/lexrh/db/      SQLAlchemy models and session
migrations/        Alembic (0001: documents, chunks, HNSW + GIN indexes)
tests/             unit tests; tests/integration needs PostgreSQL with pgvector
docs/              scope, ADRs
```

## Roadmap

Week 1 skeleton and parser → week 2 chunking and embeddings → week 3 hybrid retrieval and golden set → week 4 cited answers, auth, deployment.
