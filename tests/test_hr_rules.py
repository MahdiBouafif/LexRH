from datetime import date
from decimal import Decimal

import pytest

from lexrh.hr.rules import (
    Misconduct,
    legal_notice,
    legal_severance,
    reference_salary,
    seniority_months,
)

D = Decimal


def test_seniority_counts_whole_months() -> None:
    assert seniority_months(date(2020, 1, 15), date(2020, 2, 14)) == 0
    assert seniority_months(date(2020, 1, 15), date(2020, 2, 15)) == 1
    assert seniority_months(date(2020, 1, 31), date(2022, 1, 31)) == 24
    assert seniority_months(date(2020, 1, 1), date(2019, 1, 1)) == 0


@pytest.mark.parametrize(
    ("hired", "expected"),
    [
        (date(2026, 6, 1), None),  # 5 months: no legal figure
        (date(2026, 5, 1), 1),  # exactly 6 months
        (date(2025, 1, 2), 1),  # 22 months
        (date(2024, 11, 1), 2),  # exactly 24 months
        (date(2010, 1, 1), 2),
    ],
)
def test_notice_by_seniority(hired: date, expected: int | None) -> None:
    notice = legal_notice(hired, date(2026, 11, 1), Misconduct.NONE)
    assert notice.due and notice.months == expected


def test_no_notice_for_serious_misconduct() -> None:
    notice = legal_notice(date(2010, 1, 1), date(2026, 11, 1), Misconduct.SERIOUS)
    assert not notice.due and notice.months is None


def test_severance_five_years() -> None:
    result = legal_severance(date(2021, 11, 1), date(2026, 11, 1), D("3000"), Misconduct.NONE)
    assert result.due and result.amount == D("3750.00")  # 3000 * 5 / 4


def test_severance_beyond_ten_years() -> None:
    result = legal_severance(date(2011, 11, 1), date(2026, 11, 1), D("3000"), Misconduct.NONE)
    assert result.amount == D("12500.00")  # 3000*10/4 + 3000*5/3 = 7500 + 5000


def test_severance_pro_rata_for_incomplete_year() -> None:
    result = legal_severance(date(2025, 5, 1), date(2026, 11, 1), D("2400"), Misconduct.NONE)
    assert result.seniority_months == 18
    assert result.amount == D("900.00")  # 2400 * 1.5 / 4


def test_no_severance_under_eight_months() -> None:
    result = legal_severance(date(2026, 5, 1), date(2026, 11, 1), D("3000"), Misconduct.NONE)
    assert not result.due and result.amount == 0


def test_eight_months_is_enough() -> None:
    result = legal_severance(date(2026, 3, 1), date(2026, 11, 1), D("3000"), Misconduct.NONE)
    assert result.due and result.amount == D("500.00")  # 3000 * (8/12) / 4


@pytest.mark.parametrize("fault", [Misconduct.SERIOUS, Misconduct.GROSS])
def test_no_severance_for_serious_or_gross_misconduct(fault: Misconduct) -> None:
    result = legal_severance(date(2010, 1, 1), date(2026, 11, 1), D("3000"), fault)
    assert not result.due and result.amount == 0


def test_reference_salary_takes_the_more_favourable_average() -> None:
    twelve = [D("2000")] * 9 + [D("3000")] * 3  # average 2250
    three = [D("3000")] * 3  # average 3000
    assert reference_salary(twelve, three) == D("3000.00")
    assert reference_salary([D("3000")] * 12, [D("2000")] * 3) == D("3000.00")


def test_reference_salary_needs_data() -> None:
    with pytest.raises(ValueError):
        reference_salary([], [])


def test_results_say_they_are_legal_minimums() -> None:
    result = legal_severance(date(2021, 11, 1), date(2026, 11, 1), D("3000"), Misconduct.NONE)
    assert any("collective agreement" in note for note in result.notes)
