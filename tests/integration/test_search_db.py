from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from lexrh.rag.embed_chunks import embed_pending
from lexrh.rag.embeddings import HashEmbedder
from lexrh.rag.ingest import ingest
from lexrh.rag.search import search

pytestmark = pytest.mark.integration

D122_12 = "LEGIARTI000006644103", "LEGIARTI000006644104", "LEGIARTI000006644105"


@pytest.fixture
def engine(database_url: str, known_articles: Path):  # type: ignore[no-untyped-def]
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE documents CASCADE"))
    ingest(known_articles, eng)
    embed_pending(eng, HashEmbedder())
    yield eng
    eng.dispose()


def nums(hits) -> list[str | None]:  # type: ignore[no-untyped-def]
    return [hit.num for hit in hits]


def test_only_the_version_in_force_on_the_date_is_returned(engine) -> None:  # type: ignore[no-untyped-def]
    question = "durée du préavis"
    for as_of, expected_legal_id in (
        (date(1985, 1, 1), "LEGIARTI000006644103"),
        (date(1990, 1, 1), "LEGIARTI000006644104"),
        (date(1995, 1, 1), "LEGIARTI000006644105"),
    ):
        hits = search(engine, HashEmbedder(), question, as_of, k=20)
        d122_12 = [h for h in hits if h.num == "D122-12"]
        assert [h.legal_id for h in d122_12] == [expected_legal_id], as_of


def test_end_date_is_exclusive_and_start_date_inclusive(engine) -> None:  # type: ignore[no-untyped-def]
    # 1991-08-02 ends version ...104 and starts version ...105.
    on_the_day = search(engine, HashEmbedder(), "préavis", date(1991, 8, 2), k=20)
    day_before = search(engine, HashEmbedder(), "préavis", date(1991, 8, 1), k=20)
    assert [h.legal_id for h in on_the_day if h.num == "D122-12"] == ["LEGIARTI000006644105"]
    assert [h.legal_id for h in day_before if h.num == "D122-12"] == ["LEGIARTI000006644104"]


def test_nothing_is_returned_before_the_article_existed(engine) -> None:  # type: ignore[no-untyped-def]
    hits = search(engine, HashEmbedder(), "licenciement préavis", date(1970, 1, 1), k=20)
    assert hits == []


def test_current_articles_are_returned_today_but_not_before_they_existed(engine) -> None:  # type: ignore[no-untyped-def]
    today = search(engine, HashEmbedder(), "licenciement", date(2026, 11, 8), k=20)
    assert "L1234-1" in nums(today)
    before = search(engine, HashEmbedder(), "licenciement", date(2007, 1, 1), k=20)
    assert "L1234-1" not in nums(before)


def test_exact_chunk_text_finds_its_article_first(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        chunk_text = conn.execute(
            text(
                "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE d.num = 'L1234-1'"
            )
        ).scalar_one()
    hits = search(engine, HashEmbedder(), chunk_text, date(2026, 11, 8), k=3)
    assert hits[0].num == "L1234-1"
    assert hits[0].vector_rank == 1


def test_keyword_search_alone_can_find_an_article(engine) -> None:  # type: ignore[no-untyped-def]
    # The fake embedder knows nothing about meaning; only the French full-text can match this.
    hits = search(engine, HashEmbedder(), "congé", date(2026, 11, 8), k=20)
    assert any(hit.keyword_rank is not None for hit in hits)


def test_overlapping_versions_keep_only_the_latest_start(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        # Make version ...104 (1989-11-28 -> 1991-08-02) overlap version ...105 (from 1991-08-02).
        conn.execute(
            text("UPDATE documents SET valid_to = '2000-01-01' WHERE legal_id = :id"),
            {"id": "LEGIARTI000006644104"},
        )
    hits = search(engine, HashEmbedder(), "préavis", date(1995, 1, 1), k=20)
    assert [h.legal_id for h in hits if h.num == "D122-12"] == ["LEGIARTI000006644105"]


def test_annulled_versions_are_never_returned(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        conn.execute(text("UPDATE documents SET status = 'ANNULE' WHERE num = 'L1234-1'"))
    hits = search(engine, HashEmbedder(), "licenciement", date(2026, 11, 8), k=20)
    assert "L1234-1" not in nums(hits)


def test_one_hit_per_article_and_k_is_respected(engine) -> None:  # type: ignore[no-untyped-def]
    hits = search(engine, HashEmbedder(), "texte", date(2026, 11, 8), k=2)
    assert len(hits) <= 2
    assert len({h.document_id for h in hits}) == len(hits)


def test_empty_question_returns_nothing(engine) -> None:  # type: ignore[no-untyped-def]
    assert search(engine, HashEmbedder(), "   ", date(2026, 11, 8)) == []
