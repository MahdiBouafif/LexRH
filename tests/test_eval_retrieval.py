from datetime import date
from pathlib import Path

import pytest

from lexrh.eval.retrieval import Case, Report, evaluate, evaluate_case, format_report, load_cases
from lexrh.rag.search import SearchHit

AS_OF = date(2020, 1, 1)


def hit(num: str) -> SearchHit:
    import uuid

    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        num=num,
        legal_id="LEGIARTI0",
        valid_from=AS_OF,
        valid_to=None,
        status="VIGUEUR",
        hierarchy=[],
        nota="",
        text="",
        score=0.0,
    )


def fake_search(*nums: str):  # type: ignore[no-untyped-def]
    return lambda question, as_of, k: [hit(n) for n in nums][:k]


def case(expected: tuple[str, ...] = ("A",), id: str = "c") -> Case:
    return Case(id=id, question="q", as_of=AS_OF, expected=expected)


def test_first_rank_and_recall() -> None:
    result = evaluate_case(case(("B", "Z")), fake_search("A", "B", "C"), k=5)
    assert result.first_rank == 2
    assert result.recall == 0.5


def test_missed_case() -> None:
    result = evaluate_case(case(("Z",)), fake_search("A", "B"), k=5)
    assert result.first_rank is None
    assert result.recall == 0.0


def test_only_the_top_k_counts() -> None:
    result = evaluate_case(case(("C",)), fake_search("A", "B", "C"), k=2)
    assert result.first_rank is None


def test_report_metrics() -> None:
    report = evaluate(
        [case(("A",), "one"), case(("B",), "two"), case(("Z",), "three")],
        fake_search("A", "B"),
        k=5,
    )
    assert report.hit_rate == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx((1 + 0.5 + 0) / 3)


def test_empty_report_is_zero_not_an_error() -> None:
    report = Report(k=5, results=())
    assert (report.hit_rate, report.recall, report.mrr) == (0.0, 0.0, 0.0)


def test_format_report_lists_misses_and_flags_unverified() -> None:
    text = format_report(evaluate([case(("Z",), "lost")], fake_search("A"), k=5))
    assert "MISSED" in text
    assert "[unverified]" in text
    assert "got: A" in text


def test_load_cases_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    file = tmp_path / "cases.jsonl"
    file.write_text(
        '# comment\n\n{"id": "a", "question": "q", "as_of": "2020-01-01", "expected": ["L1"]}\n',
        encoding="utf-8",
    )
    (loaded,) = load_cases(file)
    assert loaded.expected == ("L1",)
    assert loaded.as_of == date(2020, 1, 1)
    assert loaded.verified is False


def test_load_cases_rejects_bad_line_with_its_number(tmp_path: Path) -> None:
    file = tmp_path / "cases.jsonl"
    file.write_text('{"id": "a", "question": "q"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=":1:"):
        load_cases(file)


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    line = '{"id": "a", "question": "q", "as_of": "2020-01-01", "expected": ["L1"]}\n'
    file = tmp_path / "cases.jsonl"
    file.write_text(line * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(file)


def test_the_shipped_question_file_is_valid() -> None:
    cases = load_cases(Path("evals/retrieval_questions.jsonl"))
    assert len(cases) >= 10


def test_pause_is_applied_between_cases_but_not_before_the_first() -> None:
    pauses: list[float] = []
    evaluate(
        [case(("A",), "one"), case(("A",), "two"), case(("A",), "three")],
        fake_search("A"),
        pause=2.5,
        sleep=pauses.append,
    )
    assert pauses == [2.5, 2.5]


def test_progress_is_reported_after_each_case() -> None:
    seen: list[tuple[int, int, str]] = []
    evaluate(
        [case(("A",), "one"), case(("Z",), "two")],
        fake_search("A"),
        progress=lambda done, total, result: seen.append((done, total, result.case.id)),
    )
    assert seen == [(1, 2, "one"), (2, 2, "two")]


def test_time_per_question_is_measured_and_reported() -> None:
    report = evaluate([case(("A",), "one"), case(("A",), "two")], fake_search("A"))
    assert report.mean_seconds >= 0.0
    assert report.slowest_seconds >= report.mean_seconds
    assert "time per question" in format_report(report)
