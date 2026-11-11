"""Content checks for answers: required and forbidden wording.

A case may carry
    must_contain      list of requirements; EVERY requirement must be met. A requirement is a
                      string, or a list of alternatives of which ANY one is enough
                      (["2 mois", "deux mois"]).
    must_not_contain  strings that must not appear anywhere in the answer.

Matching is case-insensitive and ignores accents-neutral spacing differences (non-breaking
spaces, curly apostrophes, repeated blanks). It is a substring check: it catches a missing
or forbidden figure, not a wrong sentence around a correct figure (min/max swapped), so a
case can also be flagged ``manual_review``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

Requirement = tuple[str, ...]  # alternatives: any one satisfies it


@dataclass(frozen=True)
class FactsResult:
    missing: tuple[Requirement, ...]  # requirements with no alternative present
    forbidden: tuple[str, ...]  # forbidden strings that were found

    @property
    def ok(self) -> bool:
        return not self.missing and not self.forbidden


def normalize(value: str) -> str:
    value = value.replace(" ", " ").replace(" ", " ").replace("’", "'")
    value = value.replace("‑", "-").replace("‐", "-")
    return re.sub(r"\s+", " ", value).strip().casefold()


def parse_requirements(raw: object) -> tuple[Requirement, ...]:
    """Accept ["a", ["b", "c"]] from JSON: strings are single-alternative requirements."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("must_contain must be a list")
    parsed: list[Requirement] = []
    for item in raw:
        if isinstance(item, str):
            parsed.append((item,))
        elif isinstance(item, list) and item and all(isinstance(x, str) for x in item):
            parsed.append(tuple(item))
        else:
            raise ValueError(f"invalid requirement: {item!r}")
    return tuple(parsed)


def parse_forbidden(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError("must_not_contain must be a list of strings")
    return tuple(raw)


def check_facts(
    answer: str, must_contain: Sequence[Requirement], must_not_contain: Sequence[str]
) -> FactsResult:
    text = normalize(answer)
    missing = tuple(
        requirement
        for requirement in must_contain
        if not any(normalize(alternative) in text for alternative in requirement)
    )
    forbidden = tuple(item for item in must_not_contain if normalize(item) in text)
    return FactsResult(missing=missing, forbidden=forbidden)
