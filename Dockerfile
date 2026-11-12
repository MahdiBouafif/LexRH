# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
# Optional extras: --build-arg EXTRAS="--extra ui --extra local-rerank" (the UI image)
ARG EXTRAS=""
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project $EXTRAS
COPY src ./src
RUN uv sync --frozen --no-dev $EXTRAS

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN mkdir -p /app/data /app/hf-cache && chown app /app/data /app/hf-cache
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status == 200 else 1)"
CMD ["uvicorn", "lexrh.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
