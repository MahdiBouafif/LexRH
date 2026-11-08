from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from lexrh.config import EMBEDDING_DIM
from lexrh.rag.embed_chunks import dry_run, embed_pending
from lexrh.rag.embeddings import HashEmbedder
from lexrh.rag.ingest import ingest

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(database_url: str, known_articles: Path):  # type: ignore[no-untyped-def]
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE documents CASCADE"))
    ingest(known_articles, eng)
    yield eng
    eng.dispose()


def _count(engine, sql: str) -> int:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar_one())


def test_dry_run_counts_and_writes_nothing(engine) -> None:  # type: ignore[no-untyped-def]
    stats = dry_run(engine)
    assert stats.documents == 8
    assert stats.chunks >= 8
    assert stats.estimated_tokens > 0
    assert _count(engine, "SELECT count(*) FROM chunks") == 0


def test_embedding_is_idempotent(engine) -> None:  # type: ignore[no-untyped-def]
    fake = HashEmbedder()
    first = embed_pending(engine, fake)
    assert first.documents == 8
    assert _count(engine, "SELECT count(*) FROM chunks") == first.chunks
    assert _count(engine, "SELECT count(*) FROM chunks WHERE embedding IS NULL") == 0

    calls_before = len(fake.calls)
    second = embed_pending(engine, fake)
    assert second.documents == 0
    assert len(fake.calls) == calls_before  # nothing was sent to the API
    assert _count(engine, "SELECT count(*) FROM chunks") == first.chunks


def test_stored_chunk_has_header_vector_and_hash(engine) -> None:  # type: ignore[no-untyped-def]
    embed_pending(engine, HashEmbedder())
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.text, vector_dims(c.embedding) AS dims, "
                "c.metadata->>'content_hash' AS hash, d.content_hash AS doc_hash "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE d.legal_id = 'LEGIARTI000006901112'"
            )
        ).one()
    assert "Article L1234-1" in row.text
    assert row.dims == EMBEDDING_DIM
    assert row.hash == row.doc_hash


def test_changed_document_gets_new_chunks(engine) -> None:  # type: ignore[no-untyped-def]
    embed_pending(engine, HashEmbedder())
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE documents SET text = 'Texte modifié.', content_hash = 'changed' "
                "WHERE legal_id = 'LEGIARTI000006901112'"
            )
        )
    second = embed_pending(engine, HashEmbedder())
    assert second.documents == 1
    with engine.connect() as conn:
        texts = (
            conn.execute(
                text(
                    "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.document_id "
                    "WHERE d.legal_id = 'LEGIARTI000006901112'"
                )
            )
            .scalars()
            .all()
        )
    assert len(texts) == 1
    assert texts[0].endswith("Texte modifié.")


def test_limit_stops_early(engine) -> None:  # type: ignore[no-untyped-def]
    stats = embed_pending(engine, HashEmbedder(), limit=3)
    assert stats.documents == 3


def test_new_chunker_version_reuses_embeddings_without_api_calls(engine) -> None:  # type: ignore[no-untyped-def]
    fake = HashEmbedder()
    first = embed_pending(engine, fake)
    # Simulate chunks written by an older chunker: version key missing.
    with engine.begin() as conn:
        conn.execute(text("UPDATE chunks SET metadata = metadata - 'chunker'"))

    calls_before = len(fake.calls)
    second = embed_pending(engine, fake)
    assert second.documents == first.documents
    assert second.reused_chunks == first.chunks
    assert len(fake.calls) == calls_before  # same text, so nothing re-sent
    assert _count(engine, "SELECT count(*) FROM chunks") == first.chunks
    assert _count(engine, "SELECT count(*) FROM chunks WHERE metadata ? 'chunker'") == first.chunks
