import json
from pathlib import Path

import pytest

from lexrh.eval.facts import check_facts, normalize, parse_forbidden, parse_requirements
from lexrh.eval.retrieval import load_cases


def req(*groups: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    return groups


def test_any_alternative_satisfies_a_requirement() -> None:
    result = check_facts("Deux mois de préavis.", req(("2 mois", "deux mois")), ())
    assert result.ok


def test_every_requirement_must_be_met() -> None:
    result = check_facts("Deux mois.", req(("2 mois", "deux mois"), ("4 mois", "quatre mois")), ())
    assert not result.ok
    assert result.missing == (("4 mois", "quatre mois"),)


def test_forbidden_wording_is_reported() -> None:
    result = check_facts("Validé par la DIRECCTE.", (), ("DIRECCTE",))
    assert result.forbidden == ("DIRECCTE",)


def test_matching_ignores_case_spaces_and_typography() -> None:
    assert normalize("10 %  Quinze JOURS l’an") == "10 % quinze jours l'an"
    assert check_facts("Soit 10 % du salaire.", req(("10 %",)), ()).ok


def test_parse_requirements_accepts_strings_and_lists() -> None:
    assert parse_requirements(["a", ["b", "c"]]) == (("a",), ("b", "c"))
    assert parse_requirements(None) == ()


@pytest.mark.parametrize("bad", ["x", [3], [[]], [["a", 2]]])
def test_parse_requirements_rejects_malformed_input(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_requirements(bad)


def test_parse_forbidden_rejects_non_strings() -> None:
    assert parse_forbidden(["a"]) == ("a",)
    with pytest.raises(ValueError):
        parse_forbidden([1])


def test_load_cases_reads_the_wording_fields(tmp_path: Path) -> None:
    line = {
        "id": "c",
        "question": "q ?",
        "as_of": "2023-01-01",
        "expected": ["L1"],
        "must_contain": [["2 mois", "deux mois"], "rétract"],
        "must_not_contain": ["DIRECCTE"],
        "manual_review": True,
    }
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(line), encoding="utf-8")
    (case,) = load_cases(path)
    assert case.must_contain == (("2 mois", "deux mois"), ("rétract",))
    assert case.must_not_contain == ("DIRECCTE",)
    assert case.manual_review is True


def test_markdown_emphasis_does_not_split_a_required_phrase() -> None:
    result = check_facts(
        "Des agissements **répétés** et `graves`.", req(("agissements répétés",)), ()
    )
    assert result.ok
