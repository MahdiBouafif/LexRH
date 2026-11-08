import shutil
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from lexrh.rag.ingest import ingest

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures"  # used by the single-file update test


@pytest.fixture
def engine(database_url: str):  # type: ignore[no-untyped-def]
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE documents CASCADE"))
    yield eng
    eng.dispose()


def test_ingestion_is_idempotent(engine, known_articles: Path) -> None:  # type: ignore[no-untyped-def]
    first = ingest(known_articles, engine)
    assert (first.inserted, first.updated, first.unchanged) == (8, 0, 0)

    second = ingest(known_articles, engine)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 8)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM documents")).scalar_one() == 8


def test_stored_row_content(engine, known_articles: Path) -> None:  # type: ignore[no-untyped-def]
    ingest(known_articles, engine)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT num, status, valid_from, valid_to, text_id, title, text, "
                "jsonb_array_length(hierarchy) AS depth "
                "FROM documents WHERE legal_id = 'LEGIARTI000006901112'"
            )
        ).one()
    assert row.num == "L1234-1"
    assert row.status == "VIGUEUR"
    assert row.valid_from == date(2008, 5, 1)
    assert row.valid_to is None
    assert row.text_id == "LEGITEXT000006072050"
    assert row.title == "Code du travail - art. L1234-1"
    assert row.text.startswith("Lorsque le licenciement")
    assert row.depth == 7


def test_changed_article_is_updated_not_duplicated(engine, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = FIXTURES / "LEGIARTI000006901112.xml"
    working = tmp_path / "LEGIARTI000006901112.xml"
    shutil.copy(source, working)
    assert ingest(tmp_path, engine).inserted == 1

    original = working.read_text(encoding="utf-8")
    working.write_text(original.replace("préavis d'un mois", "préavis de six semaines"), "utf-8")
    stats = ingest(tmp_path, engine)
    assert (stats.inserted, stats.updated, stats.unchanged) == (0, 1, 0)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        body = conn.execute(text("SELECT text FROM documents")).scalar_one()
    assert count == 1
    assert "six semaines" in body
