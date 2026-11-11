"""Mechanical checks that an answer stays inside the articles the model was shown.

The citation enum already guarantees that the *cited* numbers were provided. These checks catch
what it cannot: an answer that names an article it was not shown, or that admits in words that
it is filling a gap from memory. Either one is treated as an unusable answer (one retry with
feedback, then a refusal), because a confident answer built on missing text is the dangerous
kind in a legal assistant.

They do not verify that every sentence is supported (a wrong figure or an overstated rule with
no tell-tale wording passes); that needs a judge model or a figure-by-figure check.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ARTICLE_RE = re.compile(r"\b([LRD])\.?\s*(\d{4}-\d+(?:-\d+)*)")

# Phrases by which the model admits it goes beyond the articles. Kept narrow on purpose:
# "n'est pas mentionné ici" alone is a legitimate remark, "non cité ici" is not.
HEDGE_RE = re.compile(
    r"non\s+(?:explicitement\s+)?(?:fournie?s?|cit[ée]e?s?|d[ée]taill[ée]e?s?|mentionn[ée]e?s?)\b"
    r"|pas\s+explicitement\s+(?:mentionn|d[ée]taill|pr[ée]cis|fix)"
    r"|pratique\s+(?:administrative\s+)?courante"
    r"|g[ée]n[ée]ralement\s+interpr[ée]t[ée]",
    re.IGNORECASE,
)


def article_numbers(text: str) -> set[str]:
    """Article numbers mentioned in ``text``, normalised (``L. 3121-23`` -> ``L3121-23``)."""
    return {f"{kind}{number}" for kind, number in ARTICLE_RE.findall(text)}


def find_problem(answer: str, shown: Mapping[str, str]) -> str | None:
    """Return a short description of the problem, or None when the answer looks grounded.

    ``shown`` maps each article number shown to the model to its full text, so an article that
    the shown text itself refers to (``articles L. 3121-23 à L. 3121-25``) may be repeated.
    """
    known = set(shown)
    for body in shown.values():
        known |= article_numbers(body)
    unknown = sorted(article_numbers(answer) - known)
    if unknown:
        return "elle mentionne des articles absents de la liste : " + ", ".join(unknown)
    hedge = HEDGE_RE.search(answer)
    if hedge:
        return f"elle complète les articles de mémoire (« {hedge.group(0)} »)"
    return None
