"""Synthetic employees and cases. Invented data only: no real person is described here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Employee:
    id: str
    name: str  # synthetic; never sent to a language model
    hire_date: date
    last_12_months_pay: tuple[Decimal, ...]  # gross monthly pay, oldest first
    contract_type: str = "CDI"


@dataclass(frozen=True)
class Case:
    id: str
    employee_id: str
    type: str  # only "dismissal_personal" is supported
    fault: str  # none | serious | gross
    notification_date: date


def _pay(amount: str, months: int = 12) -> tuple[Decimal, ...]:
    return (Decimal(amount),) * months


EMPLOYEES: dict[str, Employee] = {
    "emp-001": Employee("emp-001", "Camille Test", date(2018, 3, 1), _pay("3200")),
    "emp-002": Employee("emp-002", "Alex Exemple", date(2026, 5, 4), _pay("2400", 6)),
    "emp-003": Employee("emp-003", "Sam Fictif", date(2012, 9, 15), _pay("4100")),
    "emp-004": Employee("emp-004", "Robin Factice", date(2021, 1, 11), ()),  # pay data missing
}

CASES: dict[str, Case] = {
    "case-001": Case("case-001", "emp-001", "dismissal_personal", "none", date(2026, 11, 12)),
    "case-002": Case("case-002", "emp-002", "dismissal_personal", "none", date(2026, 11, 12)),
    "case-003": Case("case-003", "emp-003", "dismissal_personal", "serious", date(2026, 11, 12)),
    "case-004": Case("case-004", "emp-004", "dismissal_personal", "none", date(2026, 11, 12)),
    "case-005": Case("case-005", "emp-001", "resignation", "none", date(2026, 11, 12)),
}
