"""Grounding checks, tested on real answers from the evaluation reports."""

import pytest

from lexrh.rag.grounding import article_numbers, find_problem

SHOWN = {
    "L3121-22": "La durée hebdomadaire [...] sauf dans les cas prévus aux articles "
    "L. 3121-23 à L. 3121-25.",
    "L1254-17": "Par dérogation à l'article L. 1254-12, la durée totale [...]",
}


def test_article_numbers_are_normalised() -> None:
    text = "Voir L. 3121-23, l'article R3121-12 et D. 1242-6, mais pas la loi 2008-596."
    assert article_numbers(text) == {"L3121-23", "R3121-12", "D1242-6"}


def test_clean_answer_passes() -> None:
    answer = "La durée moyenne est de 44 heures (L3121-22), sauf dérogation (L. 3121-23)."
    assert find_problem(answer, SHOWN) is None


def test_article_the_model_was_not_shown_is_caught() -> None:
    answer = "L'article L1243-13-1 renvoie à l'article L1242-8-1, soit 24 mois."
    problem = find_problem(answer, SHOWN)
    assert problem is not None and "L1242-8-1" in problem


@pytest.mark.parametrize(
    "answer",
    [
        "24 mois, conformément à l'article L. 1254-12 (non cité ici mais mentionné).",
        "15 jours (ce délai n'est pas explicitement mentionné dans les articles fournis, "
        "mais il est une pratique administrative courante).",
        "Repos de 24 heures, selon des principes généraux non explicitement détaillés ici.",
        "L'article renvoie à un texte non fourni ici, généralement interprété comme 24 mois.",
    ],
)
def test_admitting_to_answer_from_memory_is_caught(answer: str) -> None:
    assert find_problem(answer, SHOWN) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "Aucune limite maximale n'est explicitement fixée dans les articles fournis.",
        "Une convention collective peut prévoir plus, ce qui n'est pas mentionné ici.",
        "Le délai est de cinq jours ouvrables.",
    ],
)
def test_legitimate_remarks_are_not_flagged(answer: str) -> None:
    assert find_problem(answer, SHOWN) is None
