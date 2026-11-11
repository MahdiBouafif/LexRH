"""Explain retrieval misses: where does the expected article land BEFORE reranking?

For each case we search without the reranker, with a wide candidate list, and report the
position of each expected article in the fused ranking and in the two source rankings.

    fused <= pool            the reranker sees it: a reranker/ordering problem
    pool < fused <= wide     just outside the pool: widening the pool would catch it
    not found                neither search found it: a vocabulary gap (query rewriting)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lexrh.eval.retrieval import Case, load_cases
from lexrh.rag.search import RERANK_POOL, SearchHit

WIDE = 100
REWRITE_PAUSE = 31.0  # seconds: the rewriting model allows about 2 requests per minute


def default_pause(rewrite: bool, pause: float | None) -> float:
    """Seconds to wait between cases: explicit value, else 31 s when a model is called."""
    if pause is not None:
        return max(pause, 0.0)
    return REWRITE_PAUSE if rewrite else 0.0


@dataclass(frozen=True)
class Position:
    num: str
    fused: int | None  # 1-based rank in the fused list, None if not retrieved
    vector: int | None
    keyword: int | None


def locate(hits: Sequence[SearchHit], expected: Sequence[str]) -> list[Position]:
    positions = []
    for num in expected:
        found = next(((i, h) for i, h in enumerate(hits, start=1) if h.num == num), None)
        if found is None:
            positions.append(Position(num, None, None, None))
        else:
            rank, hit = found
            positions.append(Position(num, rank, hit.vector_rank, hit.keyword_rank))
    return positions


def verdict(position: Position, pool: int = RERANK_POOL) -> str:
    if position.fused is None:
        return "NOT FOUND (vocabulary gap: neither search retrieved it)"
    if position.fused <= pool:
        return f"in the rerank pool (fused #{position.fused})"
    return f"outside the pool (fused #{position.fused}): a wider pool would catch it"


def _rank(value: int | None) -> str:
    return f"#{value}" if value else "-"


def format_case(case: Case, positions: Sequence[Position], pool: int = RERANK_POOL) -> str:
    lines = [f"{case.id}: {case.question}"]
    for p in positions:
        lines.append(
            f"   {p.num:<10} vector {_rank(p.vector):<5} keyword {_rank(p.keyword):<5} "
            f"-> {verdict(p, pool)}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Where do expected articles land?")
    parser.add_argument("cases", nargs="?", default="evals/retrieval_questions_v2.jsonl")
    parser.add_argument("--only", nargs="*", default=[], help="case ids (default: all)")
    parser.add_argument(
        "--pause", type=float, default=None, help="seconds between cases (31 with --rewrite)"
    )
    parser.add_argument(
        "--rewrite", action="store_true", help="also search a rewritten query (needs REWRITE_MODEL)"
    )
    args = parser.parse_args(argv)

    from lexrh.config import get_settings
    from lexrh.db.session import get_engine
    from lexrh.rag.answer import build_rewriter

    logging.basicConfig(level=logging.WARNING)
    pause = default_pause(args.rewrite, args.pause)
    from lexrh.rag.embeddings import MistralEmbedder
    from lexrh.rag.search import search

    settings = get_settings()
    engine = get_engine()
    embedder = MistralEmbedder(settings.mistral_api_key)
    rewriter = build_rewriter(settings) if args.rewrite else None
    if args.rewrite and rewriter is None:
        print("REWRITE_MODEL is not set in .env", file=sys.stderr)
        return 1
    cases = [c for c in load_cases(Path(args.cases)) if not args.only or c.id in args.only]
    for index, case in enumerate(cases):
        if index and pause:
            print(f"   (waiting {pause:.0f}s for the rate limit)", flush=True)
            time.sleep(pause)
        rewritten = rewriter.rewrite(case.question, case.as_of) if rewriter else None
        hits = search(
            engine,
            embedder,
            case.question,
            case.as_of,
            k=WIDE,
            candidates=WIDE,
            extra_queries=[rewritten] if rewritten else [],
        )
        print(format_case(case, locate(hits, case.expected)))
        if rewriter:
            print(f"   rewritten: {rewritten or '(none: failed or identical, see warnings)'}")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
