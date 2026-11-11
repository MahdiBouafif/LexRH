"""The LangGraph pipeline without a database: search and article loading are replaced by fakes,
so every branch of the graph (early refusals, accept, retry, give up) is tested on its own."""

import json
import uuid
from collections.abc import Sequence
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from lexrh.rag import pipeline
from lexrh.rag.pipeline import Pipeline, run
from lexrh.rag.search import SearchHit

AS_OF = date(2026, 11, 8)
DOC_ID = uuid.uuid4()


def hit(num: str = "L1234-1", rerank_score: float | None = None) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_id=DOC_ID,
        num=num,
        legal_id="LEGIARTI000000000001",
        valid_from=date(2020, 1, 1),
        valid_to=None,
        status="VIGUEUR",
        hierarchy=["Partie", "Livre"],
        nota="",
        text="Le préavis est de deux mois.",
        score=0.5,
        rerank_score=rerank_score,
    )


def row(num: str = "L1234-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=DOC_ID,
        num=num,
        legal_id="LEGIARTI000000000001",
        text="Le préavis est de deux mois.",
        nota="",
        valid_from=date(2020, 1, 1),
        valid_to=None,
        status="VIGUEUR",
        hierarchy=["Partie", "Livre"],
    )


class ScriptedGenerator:
    """Returns the scripted replies one after the other and records every call."""

    def __init__(self, *replies: dict[str, object] | str) -> None:
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, messages: Sequence[dict[str, str]], schema: dict[str, object]) -> str:
        self.calls.append(list(messages))
        return self.replies[min(len(self.calls), len(self.replies)) - 1]


GOOD = {"answer": "Deux mois.", "citations": ["L1234-1"], "refusal": False}
NO_CITATION = {"answer": "Deux mois.", "citations": ["L9999-9"], "refusal": False}
REFUSAL = {"answer": "", "citations": [], "refusal": True}


@pytest.fixture(autouse=True)
def fake_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "search", lambda *a, **k: [hit()])
    monkeypatch.setattr(pipeline, "load_articles", lambda engine, ids: {DOC_ID: row()})


def make(generator: ScriptedGenerator, **kwargs: Any) -> Any:
    nothing: Any = None
    return Pipeline(nothing, nothing, None, generator, **kwargs).build()


def test_valid_answer_is_returned_after_one_call() -> None:
    gen = ScriptedGenerator(GOOD)
    result = run(make(gen), "préavis ?", AS_OF)
    assert not result.refused
    assert [s.num for s in result.sources] == ["L1234-1"]
    assert result.retrieved == ["L1234-1"]
    assert len(gen.calls) == 1


def test_unusable_output_is_retried_once_with_feedback() -> None:
    gen = ScriptedGenerator("pas du json", GOOD)
    result = run(make(gen), "préavis ?", AS_OF)
    assert not result.refused  # the second attempt was accepted
    assert len(gen.calls) == 2
    retry = gen.calls[1]
    assert retry[-2] == {"role": "assistant", "content": "pas du json"}
    assert "inutilisable" in retry[-1]["content"] and "L1234-1" in retry[-1]["content"]


def test_answer_without_valid_citation_is_retried_then_refused() -> None:
    gen = ScriptedGenerator(NO_CITATION, NO_CITATION)
    result = run(make(gen), "préavis ?", AS_OF)
    assert result.refused and result.reason == "no_valid_citation"
    assert result.invalid_citations == ["L9999-9"]
    assert len(gen.calls) == 2  # one retry, never more


def test_still_unusable_after_the_retry_is_refused() -> None:
    gen = ScriptedGenerator("pas du json", "toujours pas")
    result = run(make(gen), "préavis ?", AS_OF)
    assert result.refused and result.reason == "invalid_output"
    assert len(gen.calls) == 2


def test_model_refusal_is_not_retried() -> None:
    gen = ScriptedGenerator(REFUSAL, GOOD)
    result = run(make(gen), "recette de gratin ?", AS_OF)
    assert result.refused and result.reason == "model_refused"
    assert len(gen.calls) == 1


def test_retry_can_be_turned_off() -> None:
    gen = ScriptedGenerator("pas du json", GOOD)
    result = run(make(gen, max_retries=0), "préavis ?", AS_OF)
    assert result.refused and result.reason == "invalid_output"
    assert len(gen.calls) == 1


def test_nothing_retrieved_means_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "search", lambda *a, **k: [])
    gen = ScriptedGenerator(GOOD)
    result = run(make(gen), "préavis ?", AS_OF)
    assert result.refused and result.reason == "no_articles"
    assert gen.calls == []


def test_weak_relevance_means_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "search", lambda *a, **k: [hit(rerank_score=1.0)])
    gen = ScriptedGenerator(GOOD)
    result = run(make(gen, min_relevance=5.0), "préavis ?", AS_OF)
    assert result.refused and result.reason == "low_relevance"
    assert gen.calls == []


def test_rewritten_query_is_passed_to_the_search(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_search(*args: Any, **kwargs: Any) -> list[SearchHit]:
        seen.update(kwargs)
        return [hit()]

    class Rewriter:
        def rewrite(self, question: str, as_of: date) -> str | None:
            return "durée du préavis"

    monkeypatch.setattr(pipeline, "search", fake_search)
    result = run(make(ScriptedGenerator(GOOD), rewriter=Rewriter()), "combien de temps ?", AS_OF)
    assert seen["extra_queries"] == ["durée du préavis"]
    assert result.rewritten == "durée du préavis"


def test_graph_has_the_documented_nodes_and_retry_edge() -> None:
    drawing = make(ScriptedGenerator()).get_graph().draw_mermaid()
    for node in ("rewrite", "retrieve", "prepare", "generate", "verify"):
        assert node in drawing
    assert "verify -.-> generate" in drawing or "verify -.-> generate;" in drawing


def test_answer_from_memory_is_retried_then_refused() -> None:
    memory = {"answer": "Deux mois (règle non fournie ici).", "citations": ["L1234-1"]}
    gen = ScriptedGenerator({**memory, "refusal": False}, {**memory, "refusal": False})
    result = run(make(gen), "préavis ?", AS_OF)
    assert result.refused and result.reason == "unsupported_answer"
    assert len(gen.calls) == 2
    assert "de mémoire" in gen.calls[1][-1]["content"]


def test_answer_naming_an_article_not_shown_is_fixed_by_the_retry() -> None:
    bad = {"answer": "Deux mois, voir L9999-9.", "citations": ["L1234-1"], "refusal": False}
    gen = ScriptedGenerator(bad, GOOD)
    result = run(make(gen), "préavis ?", AS_OF)
    assert not result.refused and result.answer == "Deux mois."
