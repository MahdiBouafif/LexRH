"""Database fixture for integration tests.

Order of preference:
1. TEST_DATABASE_URL (used by the CI service container),
2. a throwaway pgvector container through testcontainers (needs Docker),
3. skip.
"""

import os
import shutil
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return

    if shutil.which("docker") is None:
        pytest.skip("No TEST_DATABASE_URL and no Docker available")

    from testcontainers.postgres import PostgresContainer

    try:
        container = PostgresContainer("pgvector/pgvector:pg16", driver="psycopg")
        container.start()
    except Exception as exc:  # Docker installed but daemon unreachable
        pytest.skip(f"Cannot start a Postgres container: {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()
