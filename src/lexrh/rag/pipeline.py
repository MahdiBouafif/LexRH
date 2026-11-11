"""The /ask pipeline as a LangGraph state graph.

    START -> rewrite -> retrieve -> prepare -> generate -> verify -> END
                           |           |           ^          |
                           +-- END     +-- END     +--retry---+   (at most max_retries times)

Each node does one thing and writes its result into a shared state; the edges decide what
happens next. A node that cannot continue (nothing retrieved, weak relevance, a refusal, an
unusable answer after the retry) puts the final ``Answer`` in the state and the graph stops.

Why a graph and not a function: the flow has real branches (refuse early, refuse after the
model, retry once with feedback). Each branch is an explicit edge, visible in the diagram
(``python -m lexrh.rag.pipeline``) and testable on its own.

The retry: when the model's output is unusable (not JSON, or no citation among the articles it
was shown), it gets one more attempt with a short message saying what was wrong. A refusal by
the model is never retried. After the retry the answer is refused, as before.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import Engine

from lexrh.rag.answer import (
    Answer,
    Source,
    _refuse,
    answer_schema,
    build_messages,
    format_article,
    interpret,
    load_articles,
)
from lexrh.rag.embeddings import Embedder
from lexrh.rag.generate import Generator
from lexrh.rag.grounding import find_problem
from lexrh.rag.rerank import Reranker
from lexrh.rag.rewrite import QueryRewriter
from lexrh.rag.search import DEFAULT_TOP_K, SearchHit, search

logger = logging.getLogger(__name__)

MAX_RETRIES = 1


class State(TypedDict, total=False):
    # inputs
    question: str
    as_of: date
    # filled by the nodes
    started: float
    rewritten: str | None
    hits: list[SearchHit]
    searched: float
    shown: dict[str, Any]  # article number -> article row, in the order shown to the model
    context: str
    attempt: int  # 0 for the first call to the model, 1 for the retry
    feedback: str  # what was wrong with the previous attempt
    raw: str  # the model's last output
    final: Answer  # set when the pipeline is finished


@dataclass
class Pipeline:
    """The components the nodes use, plus the settings of the flow."""

    engine: Engine
    embedder: Embedder
    reranker: Reranker | None
    generator: Generator
    k: int = DEFAULT_TOP_K
    min_relevance: float = 0.0
    rewriter: QueryRewriter | None = None
    max_retries: int = MAX_RETRIES

    # -- nodes ---------------------------------------------------------------------------

    def rewrite(self, state: State) -> dict[str, Any]:
        rewritten = (
            self.rewriter.rewrite(state["question"], state["as_of"]) if self.rewriter else None
        )
        return {"started": time.perf_counter(), "rewritten": rewritten}

    def retrieve(self, state: State) -> dict[str, Any]:
        rewritten = state["rewritten"]
        hits = search(
            self.engine,
            self.embedder,
            state["question"],
            state["as_of"],
            k=self.k,
            reranker=self.reranker,
            extra_queries=[rewritten] if rewritten else [],
        )
        update: dict[str, Any] = {"hits": hits, "searched": time.perf_counter()}
        if not hits:
            update["final"] = self._refusal(state, "no_articles", [])
        return update

    def prepare(self, state: State) -> dict[str, Any]:
        """Check relevance, load the whole articles and build the context for the prompt."""
        hits = state["hits"]
        best = hits[0].rerank_score
        if self.min_relevance > 0 and best is not None and best < self.min_relevance:
            numbers = [h.num or h.legal_id for h in hits]
            return {"final": self._refusal(state, "low_relevance", numbers)}

        articles = load_articles(self.engine, [hit.document_id for hit in hits])
        shown: dict[str, Any] = {}
        for hit in hits:
            row = articles.get(hit.document_id)
            if row is not None and row.num and row.num not in shown:
                shown[row.num] = row
        if not shown:
            return {"final": self._refusal(state, "no_articles", [])}

        context = "\n\n".join(
            format_article(num, row.valid_from, row.valid_to, row.text, row.nota, row.hierarchy)
            for num, row in shown.items()
        )
        return {"shown": shown, "context": context, "attempt": 0}

    def generate(self, state: State) -> dict[str, Any]:
        numbers = list(state["shown"])
        messages = build_messages(state["question"], state["as_of"], state["context"])
        feedback = state.get("feedback")
        if feedback:  # retry: show the model its unusable output and what was wrong with it
            messages += [
                {"role": "assistant", "content": state["raw"][:500]},
                {"role": "user", "content": feedback},
            ]
        generating = time.perf_counter()
        raw = self.generator.generate(messages, answer_schema(numbers))
        logger.info(
            "ask timing: search+rerank %.1fs, generation %.1fs "
            "(prompt %d chars, %d articles, attempt %d)",
            state["searched"] - state["started"],
            time.perf_counter() - generating,
            len(state["context"]),
            len(numbers),
            state["attempt"] + 1,
        )
        return {"raw": raw}

    def verify(self, state: State) -> dict[str, Any]:
        """Accept, refuse, or ask the model once more."""
        shown = state["shown"]
        numbers = list(shown)
        result = interpret(state["raw"], numbers)

        if result is not None and result.refusal:
            return {"final": self._refusal(state, "model_refused", numbers, result.invalid)}

        problem = None
        if result is not None and result.answer and result.citations:
            problem = find_problem(
                result.answer, {num: f"{row.text}\n{row.nota}" for num, row in shown.items()}
            )
        if result is not None and result.answer and result.citations and problem is None:
            sources = [
                Source(
                    num=num,
                    legal_id=shown[num].legal_id,
                    valid_from=shown[num].valid_from,
                    valid_to=shown[num].valid_to,
                    status=shown[num].status,
                    hierarchy=list(shown[num].hierarchy),
                )
                for num in result.citations
            ]
            answer = Answer(
                question=state["question"],
                as_of=state["as_of"],
                answer=result.answer,
                refused=False,
                reason=None,
                sources=sources,
                retrieved=numbers,
                invalid_citations=result.invalid,
                rewritten=state["rewritten"],
            )
            return {"final": answer}

        if problem is not None:
            reason = "unsupported_answer"
        else:
            reason = "invalid_output" if result is None else "no_valid_citation"
        attempt = state["attempt"]
        if attempt < self.max_retries:
            logger.info("answer unusable (%s): asking the model once more", reason)
            return {"attempt": attempt + 1, "feedback": _feedback(reason, numbers, problem)}
        if result is None:
            logger.warning("answer model returned unusable output: %.200s", state["raw"])
        return {"final": self._refusal(state, reason, numbers, result.invalid if result else None)}

    # -- edges ---------------------------------------------------------------------------

    @staticmethod
    def _end_or(next_node: str) -> Any:
        def route(state: State) -> str:
            return END if "final" in state else next_node

        return route

    def build(self) -> Any:
        graph = StateGraph(State)
        graph.add_node("rewrite", self.rewrite)
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("prepare", self.prepare)
        graph.add_node("generate", self.generate)
        graph.add_node("verify", self.verify)
        graph.add_edge(START, "rewrite")
        graph.add_edge("rewrite", "retrieve")
        graph.add_conditional_edges("retrieve", self._end_or("prepare"), ["prepare", END])
        graph.add_conditional_edges("prepare", self._end_or("generate"), ["generate", END])
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges("verify", self._end_or("generate"), ["generate", END])
        return graph.compile()

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def _refusal(
        state: State, reason: str, retrieved: list[str], invalid: list[str] | None = None
    ) -> Answer:
        return _refuse(
            reason,
            state["question"],
            state["as_of"],
            retrieved,
            invalid,
            state.get("rewritten"),
        )


def _feedback(reason: str, numbers: list[str], problem: str | None = None) -> str:
    if reason == "unsupported_answer":
        return (
            f"Ta réponse précédente n'est pas acceptable : {problem}. Réponds uniquement avec ce "
            f"que disent ces articles : {', '.join(numbers)}. Si ce n'est pas suffisant pour "
            "répondre, mets « refusal » à true plutôt que de compléter de mémoire."
        )
    problem_text = (
        "ce n'était pas un objet JSON valide"
        if reason == "invalid_output"
        else "elle ne citait aucun des articles fournis"
    )
    return (
        f"Ta réponse précédente est inutilisable : {problem_text}. Réponds à nouveau par un "
        f"objet JSON valide, en ne citant que ces articles : {', '.join(numbers)}. "
        "Si les articles ne permettent pas de répondre, mets « refusal » à true."
    )


def run(graph: Any, question: str, as_of: date) -> Answer:
    """Run the compiled graph and return its final answer."""
    state = graph.invoke({"question": question, "as_of": as_of})
    final: Answer = state["final"]
    return final


def main() -> int:
    """Print the graph as a Mermaid diagram (paste it in docs or the README)."""
    nothing: Any = None  # building the graph does not call any component
    pipeline = Pipeline(engine=nothing, embedder=nothing, reranker=None, generator=nothing)
    print(pipeline.build().get_graph().draw_mermaid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
