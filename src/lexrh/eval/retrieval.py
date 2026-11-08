"""Measure retrieval quality on a labelled question set.

A case says: "for this question, asked as of this date, these articles should be found".
For each case we run the real search and report where the expected articles landed.

Metrics (k = number of results kept, 5 by default)
    hit@k     share of cases where AT LEAST ONE expected article is in the top k
    recall@k  average share of the expected articles found in the top k
    MRR       mean of 1 / rank of the first expected article (0 when absent):
              rewards putting the right article at the top, not just somewhere in the list

The set is small and hand-checked on purpose: every expected article must be verified on
Legifrance for the given date. Numbers from an unverified set are worse than no numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lexrh.config import get_settings
from lexrh.db.session import get_engine
from lexrh.rag.embeddings import MistralEmbedder
from lexrh.rag.rerank import build_reranker
from lexrh.rag.search import DEFAULT_TOP_K, SearchHit, search

SearchFn = Callable[[str, date, int], Sequence[SearchHit]]


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    as_of: date
    expected: tuple[str, ...]  # article numbers, e.g. ("L1234-1",)
    verified: bool = False  # True once the expected articles were checked on Legifrance
    note: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: Case
    found_nums: tuple[str | None, ...]  # numbers returned, in order
    first_rank: int | None  # 1-based rank of the first expected article, None if absent
    recall: float
    seconds: float = 0.0  # wall-clock time of the whole search for this case


@dataclass(frozen=True)
class Report:
    k: int
    results: tuple[CaseResult, ...]

    @property
    def hit_rate(self) -> float:
        return _mean(1.0 if r.first_rank is not None else 0.0 for r in self.results)

    @property
    def recall(self) -> float:
        return _mean(r.recall for r in self.results)

    @property
    def mean_seconds(self) -> float:
        return _mean(r.seconds for r in self.results)

    @property
    def slowest_seconds(self) -> float:
        return max((r.seconds for r in self.results), default=0.0)

    @property
    def mrr(self) -> float:
        return _mean(1.0 / r.first_rank if r.first_rank else 0.0 for r in self.results)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def load_cases(path: Path) -> list[Case]:
    """Read one JSON object per line. Blank lines and lines starting with # are ignored."""
    cases: list[Case] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
            cases.append(
                Case(
                    id=raw["id"],
                    question=raw["question"],
                    as_of=date.fromisoformat(raw["as_of"]),
                    expected=tuple(raw["expected"]),
                    verified=bool(raw.get("verified", False)),
                    note=raw.get("note", ""),
                )
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}:{number}: invalid case ({exc})") from exc
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate case ids")
    return cases


def evaluate_case(case: Case, search_fn: SearchFn, k: int) -> CaseResult:
    started = time.perf_counter()
    hits = search_fn(case.question, case.as_of, k)
    seconds = time.perf_counter() - started
    found = tuple(hit.num for hit in hits)
    expected = set(case.expected)
    first_rank = next((i for i, num in enumerate(found, start=1) if num in expected), None)
    recall = len(expected & set(found)) / len(expected) if expected else 0.0
    return CaseResult(
        case=case, found_nums=found, first_rank=first_rank, recall=recall, seconds=seconds
    )


def evaluate(
    cases: Sequence[Case],
    search_fn: SearchFn,
    k: int = DEFAULT_TOP_K,
    *,
    pause: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[int, int, CaseResult], None] | None = None,
) -> Report:
    """Run every case. ``pause`` seconds between cases keep us under the API rate limit."""
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        if index and pause:
            sleep(pause)
        result = evaluate_case(case, search_fn, k)
        results.append(result)
        if progress is not None:
            progress(index + 1, len(cases), result)
    return Report(k=k, results=tuple(results))


def format_report(report: Report) -> str:
    unverified = sum(1 for r in report.results if not r.case.verified)
    lines = [
        f"cases: {len(report.results)}   (unverified expectations: {unverified})",
        f"hit@{report.k}:    {report.hit_rate:.0%}",
        f"recall@{report.k}: {report.recall:.0%}",
        f"MRR:       {report.mrr:.3f}",
        f"time per question: {report.mean_seconds:.1f}s average, "
        f"{report.slowest_seconds:.1f}s slowest",
        "",
    ]
    for result in report.results:
        status = f"rank {result.first_rank}" if result.first_rank else "MISSED"
        flag = "" if result.case.verified else "  [unverified]"
        lines.append(
            f"{result.case.id:<22} {status:<8} expected {', '.join(result.case.expected)}{flag}"
        )
        if result.first_rank is None or result.first_rank > 1:
            lines.append(f"{'':<22} got: {', '.join(str(n) for n in result.found_nums)}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval on a labelled set.")
    parser.add_argument("cases", nargs="?", default="evals/retrieval_questions.jsonl")
    parser.add_argument("-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--rerank", action="store_true", help="evaluate with the reranker")
    parser.add_argument("--rerank-backend", choices=["local", "mistral"], default=None)
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        help="seconds between questions (default 0)",
    )
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.cases))
    engine = get_engine()
    settings = get_settings()
    api_key = settings.mistral_api_key
    embedder = MistralEmbedder(api_key)
    backend = args.rerank_backend or settings.rerank_backend
    reranker = (
        build_reranker(
            backend,
            api_key=api_key,
            model=settings.rerank_model,
            min_interval=settings.chat_min_interval,
            local_model=settings.local_rerank_model,
        )
        if args.rerank
        else None
    )

    def run(question: str, as_of: date, k: int) -> Sequence[SearchHit]:
        return search(engine, embedder, question, as_of, k=k, reranker=reranker)

    detail = settings.local_rerank_model if backend == "local" else settings.rerank_model
    mode = f"hybrid + rerank ({backend}: {detail})" if reranker else "hybrid"
    print(f"mode: {mode}")

    def show(done: int, total: int, result: CaseResult) -> None:
        rank = f"rank {result.first_rank}" if result.first_rank else "missed"
        print(f"[{done}/{total}] {result.case.id}: {rank}", file=sys.stderr, flush=True)

    pause = args.pause if args.pause is not None else 0.0
    print(format_report(evaluate(cases, run, args.k, pause=pause, progress=show)))
    fallbacks = getattr(reranker, "fallbacks", 0)
    if fallbacks:
        print(f"\nWARNING: reranking fell back to the original order {fallbacks} time(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
