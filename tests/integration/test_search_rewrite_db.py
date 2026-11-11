from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from lexrh.rag.answer import Answer, ask
from lexrh.rag.embed_chunks import embed_pending
from lexrh.rag.embeddings import HashEmbedder
from lexrh.rag.ingest import ingest
from lexrh.rag.search import search

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 8)


@pytest.fixture
def engine(database_url: str, known_articles: Path) -> Iterator[Engine]:
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


def chunk_text(engine: Engine, num: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.document_id "
                    "WHERE d.num = :n"
                ),
                {"n": num},
            ).scalar_one()
        )


def test_extra_query_brings_in_an_article_the_question_alone_misses(engine: Engine) -> None:
    question = "zzqx wwvy"  # shares nothing with the law
    exact = chunk_text(engine, "L1234-1")
    alone = search(engine, HashEmbedder(), question, TODAY, k=3)
    boosted = search(engine, HashEmbedder(), question, TODAY, k=3, extra_queries=[exact])
    assert alone[0].num != "L1234-1"
    assert boosted[0].num == "L1234-1"


def test_no_extra_query_behaves_as_before(engine: Engine) -> None:
    a = search(engine, HashEmbedder(), "durée du préavis", TODAY, k=5)
    b = search(engine, HashEmbedder(), "durée du préavis", TODAY, k=5, extra_queries=["", "  "])
    assert [h.chunk_id for h in a] == [h.chunk_id for h in b]


class FixedRewriter:
    def __init__(self, query: str | None) -> None:
        self.query = query

    def rewrite(self, question: str, as_of: date) -> str | None:
        return self.query


class RefusingGenerator:
    def generate(self, messages, schema):  # type: ignore[no-untyped-def]
        return '{"answer": "", "citations": [], "refusal": true}'


def test_ask_records_the_rewritten_query(engine: Engine) -> None:
    result: Answer = ask(
        engine,
        HashEmbedder(),
        None,
        RefusingGenerator(),
        "zzqx",
        TODAY,
        rewriter=FixedRewriter("préavis licenciement"),
    )
    assert result.rewritten == "préavis licenciement"


def test_ask_works_when_the_rewriter_returns_nothing(engine: Engine) -> None:
    result = ask(
        engine,
        HashEmbedder(),
        None,
        RefusingGenerator(),
        "durée du préavis",
        TODAY,
        rewriter=FixedRewriter(None),
    )
    assert result.rewritten is None
