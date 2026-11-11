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
    assert "## down  [ERROR (down)]" in report
    assert "error: down" in report


def test_run_stops_after_three_errors_in_a_row_and_shows_the_cause() -> None:
    class Down:
        def ask(self, question: str, as_of: date) -> Answer:
            raise ApiError("model not found")

    lines: list[str] = []
    cases = [Case(f"c{i}", "q ?", D, ("L1",)) for i in range(6)]
    results = evaluate(Down(), cases, [], progress=lines.append)
    assert len(results) == 3
    assert "ERROR (model not found)" in lines[0]
    assert "Stopping" in lines[-1]


def test_report_lists_the_articles_shown_to_the_model() -> None:
    report = run()
    assert "shown: L1234-1" in report  # answered question
    assert "shown: -" in report  # refused question with nothing retrieved


def test_wording_checks_are_reported_for_answered_questions() -> None:
    class Fixed:
        def ask(self, question: str, as_of: date) -> Answer:
            text = "Deux mois, validé par la DIRECCTE."
            return Answer(question, as_of, text, False, None, [src("L1234-1")], ["L1234-1"])

    cases = [
        Case("good", "q ?", D, ("L1234-1",), must_contain=(("2 mois", "deux mois"),)),
        Case(
            "bad",
            "q ?",
            D,
            ("L1234-1",),
            must_contain=(("quatre mois",),),
            must_not_contain=("DIRECCTE",),
            manual_review=True,
        ),
    ]
    report = format_report(evaluate(Fixed(), cases, []))
    assert "wording checks passed:     1/2" in report
    assert "## bad  [cited expected | WORDING CHECK FAILED | MANUAL REVIEW]" in report
    assert "wording: MISSING one of ['quatre mois']" in report
    assert "wording: FORBIDDEN 'DIRECCTE' appears" in report
    assert "## good  [cited expected]" in report
