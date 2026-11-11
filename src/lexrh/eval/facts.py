"""Key-fact checks for eval answers: must_contain / must_not_contain.

Format of a question line:
  "must_contain": ["agissements répétés", ["2 mois", "deux mois"]]
      every item is required; a list item means "any one of these".
  "must_not_contain": ["DIRECCTE"]
      none of these may appear.

Matching ignores case, accents and all whitespace (including the narrow
no-break spaces French text uses before %), so "10 %", "10%" and "10 %"
all match, and "24 h" also matches "24 heures".
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("*", "")  # markdown bold must not split a fact
    return "".join(text.lower().split())


@dataclass
class FactResult:
    ok: bool
    missing: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    manual_review: bool = False


def check_facts(answer: str, item: dict) -> FactResult:
    norm = normalize(answer)
    missing: list[str] = []
    for req in item.get("must_contain", []):
        options = req if isinstance(req, list) else [req]
        if not any(normalize(o) in norm for o in options):
            missing.append(" | ".join(options))
    forbidden = [
        bad for bad in item.get("must_not_contain", []) if normalize(bad) in norm
    ]
    return FactResult(
        ok=not missing and not forbidden,
        missing=missing,
        forbidden=forbidden,
        manual_review=bool(item.get("manual_review")),
    )