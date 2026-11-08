import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from lexrh.rag.answer import Answer, ask
from lexrh.rag.embed_chunks import embed_pending
from lexrh.rag.embeddings import HashEmbedder
from lexrh.rag.generate import Generator
from lexrh.rag.ingest import ingest

pytestmark = pytest.mark.integration


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


class FakeGenerator:
    def __init__(self, reply: dict[str, object] | str) -> None:
        self.reply = reply if isinstance(reply, str) else json.dumps(reply)
        self.calls: list[tuple[list[dict[str, str]], dict[str, object]]] = []

    def generate(self, messages: Any, schema: Any) -> str:
        self.calls.append((list(messages), schema))
        return self.reply


def run(
    engine: Engine,
    gen: Generator,
    question: str = "durée du préavis",
    as_of: date = date(2026, 11, 8),
) -> Answer:
    return ask(engine, HashEmbedder(), None, gen, question, as_of)


def test_valid_citation_gives_an_answer_with_its_source(engine: Engine) -> None:
    first = None

    class Echo(FakeGenerator):
        def generate(self, messages: Any, schema: Any) -> str:
            nonlocal first
            first = schema["properties"]["citations"]["items"]["enum"][0]
            self.reply = json.dumps({"answer": "Réponse.", "citations": [first], "refusal": False})
            return super().generate(messages, schema)

    result = run(engine, Echo({}))
    assert not result.refused
    assert [s.num for s in result.sources] == [first]


def test_citation_outside_the_provided_articles_is_refused(engine: Engine) -> None:
    gen = FakeGenerator({"answer": "x", "citations": ["L9999-9"], "refusal": False})
    result = run(engine, gen)
    assert result.refused and result.reason == "no_valid_citation"
    assert result.invalid_citations == ["L9999-9"]


def test_model_refusal_is_passed_through(engine: Engine) -> None:
    gen = FakeGenerator({"answer": "", "citations": [], "refusal": True})
    assert run(engine, gen).reason == "model_refused"


def test_garbage_output_is_refused(engine: Engine) -> None:
    assert run(engine, FakeGenerator("pas du json")).reason == "invalid_output"


def test_no_article_in_force_means_no_model_call(engine: Engine) -> None:
    gen = FakeGenerator({"answer": "x", "citations": [], "refusal": False})
    result = run(engine, gen, as_of=date(1970, 1, 1))
    assert result.reason == "no_articles"
    assert gen.calls == []


def test_prompt_only_contains_articles_valid_on_the_date(engine: Engine) -> None:
    gen = FakeGenerator({"answer": "", "citations": [], "refusal": True})
    run(engine, gen, question="préavis", as_of=date(1985, 1, 1))
    prompt = gen.calls[0][0][1]["content"]
    assert "L1234-1" not in prompt  # that article did not exist in 1985
