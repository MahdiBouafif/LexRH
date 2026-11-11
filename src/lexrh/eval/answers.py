"""Measure the full /ask pipeline: right article cited, refusals, speed.

Two kinds of questions:
    answerable   (retrieval file) -> should NOT be refused, and should cite an expected article
    unanswerable (refusal file)   -> MUST be refused; an answer here is a hallucination

Metrics
    cited_expected   share of answerable questions whose answer cites at least one expected
                     article (the strongest check we can do without reading each answer)
    false_refusals   share of answerable questions the system refused
    correct_refusals share of unanswerable questions the system refused
    mean/slowest     seconds per question (search + rerank + generation)

Whether the wording of an answer is right still needs a human: the report prints every answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from lexrh.eval.facts import FactsResult, check_facts
from lexrh.eval.retrieval import Case, load_cases
from lexrh.rag.answer import Answer
from lexrh.rag.http import ApiError

MAX_CONSECUTIVE_ERRORS = 3


class Asker(Protocol):
    def ask(self, question: str, as_of: date) -> Answer: ...


@dataclass(frozen=True)
class RefusalCase:
    id: str
    question: str
    as_of: date


@dataclass(frozen=True)
class AnswerResult:
    id: str
    question: str
    expect_refusal: bool
    expected: tuple[str, ...]
    answer: Answer | None  # None when the model could not be reached
    error: str
    seconds: float
    case: Case | None = None  # the labelled case, None for refusal questions

    @property
    def facts(self) -> FactsResult | None:
        """Wording checks, only for answered questions whose case defines any."""
        case = self.case
        if case is None or self.answer is None or self.answer.refused:
            return None
        if not case.must_contain and not case.must_not_contain:
            return None
        return check_facts(self.answer.answer, case.must_contain, case.must_not_contain)

    @property
    def refused(self) -> bool:
        return self.answer is not None and self.answer.refused

    @property
    def cited(self) -> tuple[str, ...]:
        return tuple(s.num for s in self.answer.sources) if self.answer else ()

    @property
    def cited_expected(self) -> bool:
        return any(num in self.expected for num in self.cited)


def load_refusals(path: Path) -> list[RefusalCase]:
    cases: list[RefusalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            raw = json.loads(line)
            cases.append(RefusalCase(raw["id"], raw["question"], date.fromisoformat(raw["as_of"])))
    return cases


def _run(
    asker: Asker,
    id_: str,
    question: str,
    as_of: date,
    expected: tuple[str, ...],
    refusal: bool,
    case: Case | None = None,
) -> AnswerResult:
    started = time.perf_counter()
    try:
        answer, error = asker.ask(question, as_of), ""
    except ApiError as exc:
        answer, error = None, str(exc)
    return AnswerResult(
        id_, question, refusal, expected, answer, error, time.perf_counter() - started, case
    )


def evaluate(
    asker: Asker,
    answerable: Sequence[Case],
    unanswerable: Sequence[RefusalCase],
    *,
    pause: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = lambda line: None,
) -> list[AnswerResult]:
    results: list[AnswerResult] = []
    todo: list[tuple[str, str, date, tuple[str, ...], bool, Case | None]] = [
        (c.id, c.question, c.as_of, c.expected, False, c) for c in answerable
    ] + [(r.id, r.question, r.as_of, (), True, None) for r in unanswerable]
    for index, (id_, question, as_of, expected, refusal, case) in enumerate(todo, start=1):
        if index > 1 and pause:
            sleep(pause)
        result = _run(asker, id_, question, as_of, expected, refusal, case)
        results.append(result)
        progress(f"[{index}/{len(todo)}] {id_}: {_verdict(result)}")
        if len(results) >= MAX_CONSECUTIVE_ERRORS and all(
            r.answer is None for r in results[-MAX_CONSECUTIVE_ERRORS:]
        ):
            progress(
                f"Stopping: {MAX_CONSECUTIVE_ERRORS} errors in a row means the setup is wrong "
                "(model name, key or limits). Fix it, then run `make doctor`."
            )
            break
    return results


def _verdict(result: AnswerResult) -> str:
    if result.answer is None:
        return f"ERROR ({result.error[:160]})"
    if result.expect_refusal:
        return "refused (good)" if result.refused else "ANSWERED (hallucination)"
    if result.refused:
        return f"refused ({result.answer.reason})"
    verdict = "cited expected" if result.cited_expected else f"cited {list(result.cited)}"
    facts = result.facts
    if facts is not None and not facts.ok:
        verdict += " | WORDING CHECK FAILED"
    if result.case is not None and result.case.manual_review:
        verdict += " | MANUAL REVIEW"
    return verdict


def _facts_lines(result: AnswerResult) -> list[str]:
    facts = result.facts
    if facts is None:
        return []
    if facts.ok:
        return ["wording: ok"]
    lines = []
    for requirement in facts.missing:
        lines.append(f"wording: MISSING one of {list(requirement)}")
    for item in facts.forbidden:
        lines.append(f"wording: FORBIDDEN {item!r} appears")
    return lines


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "n/a"


def format_report(results: Sequence[AnswerResult]) -> str:
    answerable = [r for r in results if not r.expect_refusal]
    unanswerable = [r for r in results if r.expect_refusal]
    errors = [r for r in results if r.answer is None]
    seconds = [r.seconds for r in results]
    cited = sum(r.cited_expected for r in answerable)
    false_refusals = sum(r.refused for r in answerable)
    good_refusals = sum(r.refused for r in unanswerable)
    checked = [r for r in answerable if r.facts is not None]
    facts_ok = sum(1 for r in checked if r.facts is not None and r.facts.ok)
    lines = [
        f"answerable: {len(answerable)}   unanswerable: {len(unanswerable)}   "
        f"errors: {len(errors)}",
        f"cited an expected article: {_share(cited, len(answerable))}",
        f"wording checks passed:     {_share(facts_ok, len(checked))} "
        "(answered questions with checks)",
        f"false refusals:            {_share(false_refusals, len(answerable))}",
        f"correct refusals:          {_share(good_refusals, len(unanswerable))}",
    ]
    if seconds:
        average = sum(seconds) / len(seconds)
        lines.append(f"time per question: {average:.1f}s average, {max(seconds):.1f}s slowest")
    lines.append("")
    for r in results:
        expected = ", ".join(r.expected) or "-"
        lines.append(f"## {r.id}  [{_verdict(r)}]")
        lines.append(f"Q: {r.question}")
        if r.answer is not None and r.answer.rewritten:
            lines.append(f"searched also: {r.answer.rewritten}")
        if r.answer is None:
            lines.append(f"error: {r.error}")
        elif r.refused:
            lines.append(f"refused: {r.answer.reason}")
            lines.append(f"shown: {', '.join(r.answer.retrieved) or '-'}   expected: {expected}")
        else:
            lines.append(f"A: {r.answer.answer}")
            lines.append(f"cited: {', '.join(r.cited)}   expected: {expected}")
            lines.append(f"shown: {', '.join(r.answer.retrieved) or '-'}")
            lines.extend(_facts_lines(r))
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate /ask end to end.")
    parser.add_argument("--answerable", default="evals/retrieval_questions_v2.jsonl")
    parser.add_argument("--unanswerable", default="evals/refusal_questions.jsonl")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between questions")
    parser.add_argument("--out", default="", help="also write the report to this file")
    args = parser.parse_args(argv)

    from lexrh.rag.answer import build_assistant

    assistant = build_assistant()
    results = evaluate(
        assistant,
        load_cases(Path(args.answerable)),
        load_refusals(Path(args.unanswerable)),
        pause=args.pause,
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    report = format_report(results)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
