import uuid
from datetime import date

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def _alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migration_applies_on_clean_database(database_url: str) -> None:
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
        indexes = {row[0] for row in conn.execute(text("SELECT indexname FROM pg_indexes"))}
    assert {"documents", "chunks"} <= tables
    assert {"ix_chunks_embedding_hnsw", "ix_chunks_tsv", "ix_documents_idcc_validity"} <= indexes


def test_generated_tsvector_and_cascade(database_url: str) -> None:
    command.upgrade(_alembic_config(database_url), "head")
    engine = create_engine(database_url)
    doc_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents"
                " (id, source, legal_id, title, valid_from, status, content_hash)"
                " VALUES (:id, 'LEGI', :lid, 'Art. L1234-1', :vf, 'VIGUEUR', 'h')"
            ),
            {"id": doc_id, "lid": f"LEGIARTI{doc_id.hex[:12]}", "vf": date(2008, 5, 1)},
        )
        conn.execute(
            text(
                "INSERT INTO chunks (id, document_id, ord, text, metadata)"
                " VALUES (:id, :did, 0, 'Le préavis de licenciement', '{}')"
            ),
            {"id": chunk_id, "did": doc_id},
        )
        hits = conn.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('french', 'préavis')")
        ).scalar_one()
        assert hits == 1

        conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
        left = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
        assert left == 0
