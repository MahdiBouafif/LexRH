from datetime import date

from lexrh.eval.answers import RefusalCase, evaluate, format_report
from lexrh.eval.retrieval import Case
from lexrh.rag.answer import Answer, Source
from lexrh.rag.http import ApiError

D = date(2023, 1, 1)


def src(num: str) -> Source:
    return Source(num, "ID", D, None, "VIGUEUR", [])


class FakeAsker:
    def ask(self, question: str, as_of: date) -> Answer:
        if "panne" in question:
            raise ApiError("down")
        if "impôt" in question:
            return Answer(question, as_of, "Voici.", False, None, [src("L1")], ["L1"])
        if "hors" in question:
            return Answer(question, as_of, "", True, "model_refused")
        return Answer(question, as_of, "Oui.", False, None, [src("L1234-1")], ["L1234-1"])


def run() -> str:
    answerable = [
        Case("ok", "préavis ?", D, ("L1234-1",)),
        Case("wrong", "autre ?", D, ("L9999-9",)),
        Case("false-refusal", "hors sujet ?", D, ("L1",)),
        Case("down", "panne ?", D, ("L1",)),
    ]
    unanswerable = [RefusalCase("halluc", "impôt ?", D), RefusalCase("good", "hors ?", D)]
    results = evaluate(FakeAsker(), answerable, unanswerable)
    return format_report(results)


def test_report_counts_each_kind_of_outcome() -> None:
    report = run()
    assert "answerable: 4   unanswerable: 2   errors: 1" in report
    assert "cited an expected article: 1/4 (25%)" in report
    assert "false refusals:            1/4 (25%)" in report
    assert "correct refusals:          1/2 (50%)" in report


def test_report_flags_hallucinations_and_errors() -> None:
    report = run()
    assert "## halluc  [ANSWERED (hallucination)]" in report
    assert "## down  [ERROR]" in report
    assert "error: down" in report
