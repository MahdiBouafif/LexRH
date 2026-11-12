"""Deterministic HR calculations: legal notice period and legal severance pay.

Plain code, no LLM: the money decision must be reproducible and unit-tested. These are the
LEGAL MINIMUMS of the Code du travail for a dismissal for a personal reason (L1234-1, L1234-9,
R1234-1, R1234-2, R1234-4). A collective agreement or the contract may be more favourable and
is NOT applied here; the result says so in ``notes``.

Rules implemented
    notice (L1234-1)      seniority < 6 months   -> set by agreement or custom (no legal figure)
                          6 months <= s < 2 years -> 1 month
                          s >= 2 years            -> 2 months
                          none for serious / gross misconduct (faute grave ou lourde)
    severance             requires 8 months of seniority (L1234-9), none for faute grave/lourde
    (R1234-2)             1/4 of a month of reference salary per year up to 10 years,
                          plus 1/3 of a month per year beyond 10 years, pro rata for
                          incomplete years (R1234-1)
    reference salary      the more favourable of the average of the last 12 months and the
    (R1234-4)             average of the last 3 months (bonuses over 3 months pro rata)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

MIN_SENIORITY_MONTHS_SEVERANCE = 8
CENT = Decimal("0.01")


class Misconduct(StrEnum):
    NONE = "none"
    SERIOUS = "serious"  # faute grave
    GROSS = "gross"  # faute lourde


@dataclass(frozen=True)
class Notice:
    months: int | None  # None: no legal figure (agreement or custom decides)
    due: bool
    articles: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Severance:
    amount: Decimal
    due: bool
    seniority_months: int
    reference_salary: Decimal
    articles: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def seniority_months(hire_date: date, as_of: date) -> int:
    """Whole months between the hire date and ``as_of`` (0 if ``as_of`` is earlier)."""
    if as_of <= hire_date:
        return 0
    months = (as_of.year - hire_date.year) * 12 + (as_of.month - hire_date.month)
    if as_of.day < hire_date.day:
        months -= 1
    return max(months, 0)


def reference_salary(last_12_months: list[Decimal], last_3_months: list[Decimal]) -> Decimal:
    """More favourable of the 12-month and the 3-month monthly average (R1234-4).

    Each list holds the gross monthly pay of those months (bonuses already pro-rated by the
    caller). Fewer months are accepted for an employee with less seniority.
    """
    if not last_12_months and not last_3_months:
        raise ValueError("at least one month of pay is needed")
    averages = [
        sum(values, Decimal(0)) / len(values)
        for values in (last_12_months, last_3_months)
        if values
    ]
    return max(averages).quantize(CENT, ROUND_HALF_UP)


def legal_notice(hire_date: date, notification: date, misconduct: Misconduct) -> Notice:
    if misconduct is not Misconduct.NONE:
        return Notice(
            None,
            False,
            ("L1234-1",),
            ("No notice period is due in case of serious or gross misconduct.",),
        )
    months = seniority_months(hire_date, notification)
    notes = ("Legal minimum only: a collective agreement or the contract may be more favourable.",)
    if months < 6:
        return Notice(
            None,
            True,
            ("L1234-1",),
            ("Under 6 months of seniority the law sets no figure: see agreement or custom.",)
            + notes,
        )
    return Notice(1 if months < 24 else 2, True, ("L1234-1",), notes)


def legal_severance(
    hire_date: date,
    notification: date,
    salary: Decimal,
    misconduct: Misconduct,
) -> Severance:
    """Legal severance for a dismissal for a personal reason, from the reference ``salary``."""
    months = seniority_months(hire_date, notification)
    articles = ("L1234-9", "R1234-1", "R1234-2")
    notes = ("Legal minimum only: a collective agreement or the contract may be more favourable.",)
    if misconduct is not Misconduct.NONE:
        return Severance(
            Decimal(0),
            False,
            months,
            salary,
            articles,
            ("No legal severance in case of serious or gross misconduct.",),
        )
    if months < MIN_SENIORITY_MONTHS_SEVERANCE:
        return Severance(
            Decimal(0),
            False,
            months,
            salary,
            articles,
            (f"Less than {MIN_SENIORITY_MONTHS_SEVERANCE} months of seniority.",),
        )
    years = Decimal(months) / 12  # incomplete years count pro rata
    first = min(years, Decimal(10))
    beyond = max(years - 10, Decimal(0))
    amount = (salary * first / 4 + salary * beyond / 3).quantize(CENT, ROUND_HALF_UP)
    return Severance(amount, True, months, salary, articles, notes)
